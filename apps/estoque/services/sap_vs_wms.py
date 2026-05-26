"""Conciliação SAP vs WMS: importação e comparação de saldos."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import BinaryIO

import pandas as pd
from django.db import transaction
from django.db.models import Max, Q, Sum

from apps.estoque.models import EstoqueFisico, SapVsWmsUpload
from apps.produtos.models import Produto
from apps.recebimento.models import EstoqueTemporario


class SapVsWmsError(Exception):
    pass


class StatusConciliacao:
    OK = 'OK'
    DIVERGENTE = 'DIVERGENTE'
    SEM_SAP = 'SEM SAP'
    SEM_WMS = 'SEM WMS'


@dataclass
class LinhaConciliacao:
    codigo_produto: str
    descricao: str
    quantidade_wms: Decimal
    quantidade_sap: Decimal
    diferenca: Decimal
    status: str
    setor: str


@dataclass
class MetricasConciliacao:
    total_litros_wms: Decimal
    acuracidade_pct: Decimal
    total_divergentes: int
    total_linhas: int


def normalizar_codigo_produto(valor) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ''
    if isinstance(valor, float) and valor == int(valor):
        return str(int(valor))
    if isinstance(valor, int):
        return str(valor)
    texto = str(valor).strip()
    if texto.endswith('.0') and texto[:-2].isdigit():
        return texto[:-2]
    return texto


COLUNA_CODIGO_ALIASES = frozenset(
    {'CODPRODUTO', 'CODPRODUT', 'COD_PRODUTO', 'CODIGO', 'CODIGO_PRODUTO', 'CODPROD'}
)
COLUNA_DESCRICAO_ALIASES = frozenset({'DESCRICAO', 'DESC'})
COLUNA_TOTAL_OFICIAL = 'TOTAL'


def _normalizar_header(coluna) -> str:
    """Normaliza cabeçalho: strip, upper, sem espaços extras/caracteres invisíveis."""
    if coluna is None or (isinstance(coluna, float) and pd.isna(coluna)):
        texto = ''
    else:
        texto = str(coluna)
    texto = unicodedata.normalize('NFKD', texto)
    texto = ''.join(ch for ch in texto if not unicodedata.combining(ch))
    texto = texto.replace('\ufeff', '').replace('\n', '').replace('\r', '').replace('\t', ' ')
    texto = re.sub(r'[\u200b-\u200d\ufeff]', '', texto)
    texto = ' '.join(texto.split())
    return texto.strip().upper()


def _normalizar_colunas_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [_normalizar_header(col) for col in df.columns]
    return df


def _planilha_conciliacao_valida(colunas: list[str]) -> bool:
    """Aba válida: coluna TOTAL exata (não 'Total da linha') + código produto."""
    cols = set(colunas)
    if COLUNA_TOTAL_OFICIAL not in cols:
        return False
    return any(col in cols for col in COLUNA_CODIGO_ALIASES)


def _coluna_codigo(colunas: list[str]) -> str | None:
    for col in colunas:
        if col in COLUNA_CODIGO_ALIASES:
            return col
    return colunas[0] if colunas else None


def _coluna_descricao(colunas: list[str]) -> str | None:
    for col in colunas:
        if col in COLUNA_DESCRICAO_ALIASES:
            return col
    return None


def _coluna_total(colunas: list[str]) -> str | None:
    """Saldo SAP oficial: somente coluna TOTAL (nome exato após normalização)."""
    if COLUNA_TOTAL_OFICIAL in colunas:
        return COLUNA_TOTAL_OFICIAL
    return None


def _carregar_dataframe_planilha_sap(arquivo: BinaryIO) -> pd.DataFrame:
    """
    Carrega a aba de conciliação (CodProduto + Total).
    Planilhas com várias abas: ignora export SAP e usa a aba correta.
    """
    try:
        excel = pd.ExcelFile(arquivo)
    except Exception as exc:
        raise SapVsWmsError(f'Não foi possível ler a planilha: {exc}') from exc

    candidatos: list[pd.DataFrame] = []
    for nome_aba in excel.sheet_names:
        bruta = pd.read_excel(excel, sheet_name=nome_aba, dtype=object)
        if bruta.empty:
            continue
        normalizada = _normalizar_colunas_dataframe(bruta)
        if _planilha_conciliacao_valida(list(normalizada.columns)):
            return normalizada
        candidatos.append(normalizada)

    if candidatos:
        for df in candidatos:
            if COLUNA_TOTAL_OFICIAL in df.columns:
                return df

    if excel.sheet_names:
        return _normalizar_colunas_dataframe(pd.read_excel(excel, sheet_name=0, dtype=object))
    raise SapVsWmsError('Planilha vazia.')


def _mapa_codigo_produto_numerico() -> dict[int, str]:
    mapa: dict[int, str] = {}
    for cod_prod, codigo in Produto.objects.values_list('cod_prod', 'codigo'):
        for valor in (cod_prod, codigo):
            if not valor:
                continue
            texto = str(valor).strip()
            if texto.isdigit():
                mapa[int(texto)] = texto
    return mapa


def alinhar_codigo_cadastro_wms(codigo: str, mapa_numerico: dict[int, str] | None = None) -> str:
    """Alinha código da planilha ao cadastro WMS (ex.: Excel 2005 → 20005)."""
    if not codigo:
        return codigo
    if (
        Produto.objects.filter(Q(cod_prod=codigo) | Q(codigo=codigo)).exists()
        or EstoqueFisico.objects.filter(codigo_produto=codigo).exists()
    ):
        return codigo
    if not codigo.isdigit():
        return codigo

    candidatos_wms = sorted(
        {
            str(cp)
            for cp in EstoqueFisico.objects.filter(
                status=EstoqueFisico.Status.ATIVO,
                quantidade__gt=0,
                codigo_produto__startswith=codigo,
            )
            .exclude(codigo_produto=codigo)
            .values_list('codigo_produto', flat=True)
        },
        key=len,
    )
    if len(candidatos_wms) == 1:
        return candidatos_wms[0]
    if candidatos_wms:
        menor = candidatos_wms[0]
        if sum(1 for cp in candidatos_wms if len(cp) == len(menor)) == 1:
            return menor

    mapa = mapa_numerico if mapa_numerico is not None else _mapa_codigo_produto_numerico()
    candidato = mapa.get(int(codigo))
    if candidato and candidato == codigo:
        return candidato
    return codigo


def _parse_decimal(valor) -> Decimal:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return Decimal('0')
    try:
        return Decimal(str(valor).replace(',', '.')).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return Decimal('0')


def importar_planilha_sap(arquivo: BinaryIO, usuario) -> int:
    """Substitui integralmente o snapshot SAP anterior."""
    df = _carregar_dataframe_planilha_sap(arquivo)
    if df.empty:
        raise SapVsWmsError('Planilha vazia.')

    colunas = list(df.columns)
    cod_col = _coluna_codigo(colunas)
    desc_col = _coluna_descricao(colunas)
    total_col = _coluna_total(colunas)
    if not cod_col:
        raise SapVsWmsError('Coluna de código do produto não encontrada (ex.: CodProduto).')
    if not total_col:
        raise SapVsWmsError(
            'Coluna TOTAL não encontrada. O saldo SAP deve estar na coluna "Total" '
            '(aba com CodProduto e Total, não export SAP genérico).'
        )

    mapa_codigos = _mapa_codigo_produto_numerico()
    agregado: dict[str, dict] = {}
    for _, row in df.iterrows():
        codigo = normalizar_codigo_produto(row[cod_col])
        if not codigo:
            continue
        codigo = alinhar_codigo_cadastro_wms(codigo, mapa_codigos)
        quantidade = _parse_decimal(row[total_col])
        descricao = ''
        if desc_col is not None:
            descricao = str(row.get(desc_col) or '').strip()[:255]
        if codigo in agregado:
            agregado[codigo]['quantidade_sap'] = quantidade
            if descricao and not agregado[codigo]['descricao']:
                agregado[codigo]['descricao'] = descricao
        else:
            agregado[codigo] = {
                'codigo_produto': codigo,
                'descricao': descricao or codigo,
                'quantidade_sap': quantidade,
                'setor': '',
            }

    if not agregado:
        raise SapVsWmsError('Nenhum produto válido encontrado na planilha.')

    registros = [
        SapVsWmsUpload(
            codigo_produto=item['codigo_produto'],
            descricao=item['descricao'],
            quantidade_sap=item['quantidade_sap'],
            setor='',
            usuario_upload=usuario,
        )
        for item in agregado.values()
    ]

    with transaction.atomic():
        SapVsWmsUpload.objects.all().delete()
        SapVsWmsUpload.objects.bulk_create(registros, batch_size=500)

    return len(registros)


def _mapa_setores_produtos(codigos: set[str]) -> dict[str, str]:
    if not codigos:
        return {}
    produtos = Produto.objects.filter(
        Q(cod_prod__in=codigos) | Q(codigo__in=codigos),
    ).only('cod_prod', 'codigo', 'setor')
    mapa: dict[str, str] = {}
    for produto in produtos:
        setor = (produto.setor or '').strip()
        for cod in (produto.cod_prod, produto.codigo):
            if cod:
                mapa[normalizar_codigo_produto(cod)] = setor
    return mapa


def _calcular_status(sap: Decimal, wms: Decimal) -> str:
    if sap > 0 and wms == 0:
        return StatusConciliacao.SEM_WMS
    if wms > 0 and sap == 0:
        return StatusConciliacao.SEM_SAP
    if sap == wms:
        return StatusConciliacao.OK
    return StatusConciliacao.DIVERGENTE


def montar_linhas_conciliacao(*, busca: str = '', setor: str = '') -> list[LinhaConciliacao]:
    wms_fisico_rows = (
        EstoqueFisico.objects.filter(status=EstoqueFisico.Status.ATIVO)
        .values('codigo_produto')
        .annotate(
            quantidade_wms=Sum('quantidade'),
            descricao_wms=Max('descricao'),
        )
    )
    wms_temp_rows = (
        EstoqueTemporario.objects.filter(
            status=EstoqueTemporario.Status.TEMP,
            quantidade__gt=0,
        )
        .values('produto_codigo')
        .annotate(
            quantidade_temp=Sum('quantidade'),
            descricao_temp=Max('descricao'),
        )
    )
    sap_rows = SapVsWmsUpload.objects.values('codigo_produto').annotate(
        quantidade_sap=Sum('quantidade_sap'),
        descricao_sap=Max('descricao'),
    )

    wms_map = {
        normalizar_codigo_produto(r['codigo_produto']): r
        for r in wms_fisico_rows
        if r['codigo_produto']
    }
    temp_map = {
        normalizar_codigo_produto(r['produto_codigo']): r
        for r in wms_temp_rows
        if r['produto_codigo']
    }
    sap_map = {
        normalizar_codigo_produto(r['codigo_produto']): r
        for r in sap_rows
        if r['codigo_produto']
    }
    codigos = set(wms_map) | set(temp_map) | set(sap_map)
    setores_map = _mapa_setores_produtos(codigos)

    busca_l = (busca or '').strip().lower()
    setor_f = (setor or '').strip()

    linhas: list[LinhaConciliacao] = []
    for codigo in sorted(codigos):
        wms_row = wms_map.get(codigo, {})
        temp_row = temp_map.get(codigo, {})
        sap_row = sap_map.get(codigo, {})
        qtd_fisico = _parse_decimal(wms_row.get('quantidade_wms'))
        qtd_temp = _parse_decimal(temp_row.get('quantidade_temp'))
        qtd_wms = qtd_fisico + qtd_temp
        qtd_sap = _parse_decimal(sap_row.get('quantidade_sap'))

        descricao = (
            (sap_row.get('descricao_sap') or '').strip()
            or (wms_row.get('descricao_wms') or '').strip()
            or (temp_row.get('descricao_temp') or '').strip()
            or codigo
        )
        setor_prod = setores_map.get(codigo, '')

        if busca_l and busca_l not in codigo.lower() and busca_l not in descricao.lower():
            continue
        if setor_f and setor_prod != setor_f:
            continue

        linhas.append(
            LinhaConciliacao(
                codigo_produto=codigo,
                descricao=descricao,
                quantidade_wms=qtd_wms,
                quantidade_sap=qtd_sap,
                diferenca=qtd_wms - qtd_sap,
                status=_calcular_status(qtd_sap, qtd_wms),
                setor=setor_prod or '-',
            )
        )
    return linhas


def calcular_metricas(linhas: list[LinhaConciliacao]) -> MetricasConciliacao:
    total_litros = sum((linha.quantidade_wms for linha in linhas), Decimal('0'))
    total = len(linhas)
    if total == 0:
        return MetricasConciliacao(
            total_litros_wms=total_litros,
            acuracidade_pct=Decimal('0'),
            total_divergentes=0,
            total_linhas=0,
        )
    corretos = sum(1 for linha in linhas if linha.status == StatusConciliacao.OK)
    divergentes = sum(1 for linha in linhas if linha.status == StatusConciliacao.DIVERGENTE)
    acuracidade = (Decimal(corretos) / Decimal(total) * Decimal('100')).quantize(Decimal('0.1'))
    return MetricasConciliacao(
        total_litros_wms=total_litros,
        acuracidade_pct=acuracidade,
        total_divergentes=divergentes,
        total_linhas=total,
    )


def listar_setores_disponiveis() -> list[str]:
    return sorted(
        {
            (s or '').strip()
            for s in Produto.objects.exclude(setor__isnull=True)
            .exclude(setor='')
            .values_list('setor', flat=True)
            .distinct()
            if (s or '').strip()
        }
    )
