"""Conciliação SAP vs WMS: importação e comparação de saldos."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import BinaryIO

import pandas as pd
from django.db import transaction
from django.db.models import Max, Q, Sum

from apps.estoque.models import EstoqueFisico, SapVsWmsUpload
from apps.produtos.models import Produto


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


def _coluna_codigo(colunas: list[str]) -> str | None:
    aliases = {
        'codproduto',
        'cod_produto',
        'codigo',
        'codigo_produto',
        'cod produto',
        'codprod',
    }
    for col in colunas:
        if col.strip().lower().replace(' ', '') in aliases or col.strip().lower() in aliases:
            return col
    return colunas[0] if colunas else None


def _coluna_descricao(colunas: list[str]) -> str | None:
    for col in colunas:
        if col.strip().lower() in ('descricao', 'descrição', 'desc'):
            return col
    return colunas[1] if len(colunas) > 1 else None


def _coluna_total(colunas: list[str]) -> str | None:
    for col in colunas:
        if col.strip().lower() == 'total':
            return col
    return colunas[-1] if colunas else None


def _parse_decimal(valor) -> Decimal:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return Decimal('0')
    try:
        return Decimal(str(valor).replace(',', '.')).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return Decimal('0')


def importar_planilha_sap(arquivo: BinaryIO, usuario) -> int:
    """Substitui integralmente o snapshot SAP anterior."""
    try:
        df = pd.read_excel(arquivo, dtype=object)
    except Exception as exc:
        raise SapVsWmsError(f'Não foi possível ler a planilha: {exc}') from exc

    if df.empty:
        raise SapVsWmsError('Planilha vazia.')

    df.columns = [str(c).strip() for c in df.columns]
    cod_col = _coluna_codigo(list(df.columns))
    desc_col = _coluna_descricao(list(df.columns))
    total_col = _coluna_total(list(df.columns))
    if not cod_col or not total_col:
        raise SapVsWmsError('Colunas obrigatórias não encontradas (código produto e Total).')

    agregado: dict[str, dict] = {}
    for _, row in df.iterrows():
        codigo = normalizar_codigo_produto(row.get(cod_col))
        if not codigo:
            continue
        quantidade = _parse_decimal(row.get(total_col))
        descricao = ''
        if desc_col is not None:
            descricao = str(row.get(desc_col) or '').strip()[:255]
        if codigo in agregado:
            agregado[codigo]['quantidade_sap'] += quantidade
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
    wms_rows = (
        EstoqueFisico.objects.filter(status=EstoqueFisico.Status.ATIVO)
        .values('codigo_produto')
        .annotate(
            quantidade_wms=Sum('quantidade'),
            descricao_wms=Max('descricao'),
        )
    )
    sap_rows = SapVsWmsUpload.objects.values('codigo_produto').annotate(
        quantidade_sap=Sum('quantidade_sap'),
        descricao_sap=Max('descricao'),
    )

    wms_map = {
        normalizar_codigo_produto(r['codigo_produto']): r
        for r in wms_rows
        if r['codigo_produto']
    }
    sap_map = {
        normalizar_codigo_produto(r['codigo_produto']): r
        for r in sap_rows
        if r['codigo_produto']
    }
    codigos = set(wms_map) | set(sap_map)
    setores_map = _mapa_setores_produtos(codigos)

    busca_l = (busca or '').strip().lower()
    setor_f = (setor or '').strip()

    linhas: list[LinhaConciliacao] = []
    for codigo in sorted(codigos):
        wms_row = wms_map.get(codigo, {})
        sap_row = sap_map.get(codigo, {})
        qtd_wms = _parse_decimal(wms_row.get('quantidade_wms'))
        qtd_sap = _parse_decimal(sap_row.get('quantidade_sap'))
        if qtd_wms == 0 and qtd_sap == 0:
            continue

        descricao = (
            (sap_row.get('descricao_sap') or '').strip()
            or (wms_row.get('descricao_wms') or '').strip()
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
