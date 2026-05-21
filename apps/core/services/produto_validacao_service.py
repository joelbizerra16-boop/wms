import logging
import re
from dataclasses import dataclass

from django.db import OperationalError, connection
from django.db.models import Q
from django.utils import timezone

from django.core.cache import cache

from apps.core.bipagem_leitura import (
    codigo_bipagem_primario,
    normalizar_codigo_barras as normalizar_codigo_barras_leitura,
    sanitizar_entrada_scanner,
    variantes_codigo_barras,
)
from apps.core.db_telemetry import registrar_cache_hit, registrar_cache_miss
from apps.logs.models import Log
from apps.produtos.models import Produto

PRODUTO_LOOKUP_CACHE_TTL = 300

logger = logging.getLogger(__name__)


class ProdutoValidacaoError(Exception):
    pass


@dataclass
class ProdutoValidado:
    produto: Produto
    item: object
    codigo_lido_normalizado: str
    codigo_esperado: str
    setor_validado: str


def normalizar_codigo_barras(codigo):
    """Extrai dígitos da leitura e usa os 14 últimos quando o scanner envia prefixo extra."""
    return normalizar_codigo_barras_leitura(codigo)


def buscar_produto_por_leitura(codigo_lido, *, modulo=None):
    codigo_normalizado = _normalizar_codigo(codigo_lido, modulo=modulo)
    if not codigo_normalizado:
        return None
    return _buscar_produto_por_codigo(codigo_normalizado)


def selecionar_item_por_codigo_lido(codigo_lido, itens, *, fallback=None, modulo=None):
    codigo_normalizado = _normalizar_codigo(codigo_lido, modulo=modulo)
    if not codigo_normalizado:
        return fallback
    for item in itens:
        produto = getattr(item, 'produto', None)
        if produto and _codigo_corresponde_identificador(codigo_normalizado, _identificadores_produto(produto)):
            return item
    return fallback


def filtrar_queryset_por_codigo_produto(queryset, codigo_lido, *, prefixo_produto='produto__', modulo=None):
    codigo_normalizado = _normalizar_codigo(codigo_lido, modulo=modulo)
    variantes = _codigo_variantes(codigo_normalizado)
    if not variantes:
        return queryset.none(), codigo_normalizado

    filtro = Q()
    for variante in variantes:
        filtro |= Q(**{f'{prefixo_produto}cod_ean': variante})
        filtro |= Q(**{f'{prefixo_produto}cod_prod': variante})
        filtro |= Q(**{f'{prefixo_produto}codigo': variante})
    return queryset.filter(filtro), codigo_normalizado


def validar_produto(codigo_lido, item_id, usuario, item_model, tipo_validacao, *, item_travado=None):
    modulo = 'separacao' if tipo_validacao == 'SEPARACAO' else 'conferencia'
    codigo_normalizado = _normalizar_codigo(codigo_lido, modulo=modulo)
    if not codigo_normalizado:
        raise ProdutoValidacaoError('Informe um código válido para bipagem.')

    if item_travado is not None:
        item = item_travado
    else:
        lock_kwargs = {}
        if connection.vendor == 'postgresql':
            lock_kwargs = {'of': ('self',)}
        item = (
            item_model.objects.select_for_update(**lock_kwargs)
            .select_related('produto')
            .get(id=item_id)
        )
    produto_esperado = item.produto
    identificadores_esperados = _identificadores_produto(produto_esperado)
    if _codigo_corresponde_identificador(codigo_normalizado, identificadores_esperados):
        produto = produto_esperado
    else:
        produto = _buscar_produto_por_codigo(codigo_normalizado)
    if not produto:
        _log_validacao(
            usuario=usuario,
            tipo_validacao=tipo_validacao,
            codigo_lido=codigo_lido,
            produto_encontrado_id=None,
            item_id=item_id,
            produto_esperado_id=produto_esperado.id,
            produto_setor='',
            item_setor=(produto_esperado.setor or '').strip().upper(),
            resultado='produto_nao_cadastrado',
        )
        raise ProdutoValidacaoError('Produto não cadastrado.')

    codigo_esperado = _codigo_exibicao_produto(item.produto)
    produto_esperado_id = item.produto_id
    item_setor = (item.produto.setor or '').strip().upper()
    produto_setor = (produto.setor or '').strip().upper()

    if produto.id != produto_esperado_id:
        # Revalidação de proteção para reduzir falso negativo em concorrência.
        item_revalidado = (
            item_model.objects.select_related('produto')
            .get(id=item_id)
        )
        if produto.id == item_revalidado.produto_id:
            _log_validacao(
                usuario=usuario,
                tipo_validacao=tipo_validacao,
                codigo_lido=codigo_lido,
                produto_encontrado_id=produto.id,
                item_id=item_id,
                produto_esperado_id=item_revalidado.produto_id,
                produto_setor=produto_setor,
                item_setor=(item_revalidado.produto.setor or '').strip().upper(),
                resultado='aceito_pos_revalidacao',
            )
            return ProdutoValidado(
                produto=produto,
                item=item_revalidado,
                codigo_lido_normalizado=codigo_normalizado,
                codigo_esperado=_codigo_exibicao_produto(item_revalidado.produto),
                setor_validado=(item_revalidado.produto.setor or '').strip().upper(),
            )

        _log_validacao(
            usuario=usuario,
            tipo_validacao=tipo_validacao,
            codigo_lido=codigo_lido,
            produto_encontrado_id=produto.id,
            item_id=item_id,
            produto_esperado_id=item_revalidado.produto_id,
            produto_setor=produto_setor,
            item_setor=(item_revalidado.produto.setor or '').strip().upper(),
            resultado='produto_divergente',
        )
        raise ProdutoValidacaoError(
            f"Produto lido ({codigo_normalizado}) não corresponde ao item esperado "
            f"({_codigo_exibicao_produto(item_revalidado.produto)}) - verificar cadastro ou sincronização"
        )

    if produto_setor != item_setor:
        _log_validacao(
            usuario=usuario,
            tipo_validacao=tipo_validacao,
            codigo_lido=codigo_lido,
            produto_encontrado_id=produto.id,
            item_id=item_id,
            produto_esperado_id=produto_esperado_id,
            produto_setor=produto_setor,
            item_setor=item_setor,
            resultado='setor_divergente',
        )
        raise ProdutoValidacaoError(
            f'Produto do setor {produto_setor or "-"} não corresponde ao item do setor {item_setor or "-"}'
        )

    return ProdutoValidado(
        produto=produto,
        item=item,
        codigo_lido_normalizado=codigo_normalizado,
        codigo_esperado=codigo_esperado,
        setor_validado=item_setor,
    )


def _entrada_e_leitura_numerica(valor):
    compacto = ''.join(str(valor or '').strip().split())
    return bool(compacto) and compacto.isdigit()


def _log_codigo_normalizado(codigo_original, codigo_normalizado, modulo):
    if codigo_original == codigo_normalizado:
        return
    logger.info(
        'CODIGO_NORMALIZADO codigo_original=%s codigo_normalizado=%s modulo=%s',
        codigo_original,
        codigo_normalizado,
        modulo or 'operacional',
    )


def _normalizar_codigo(valor, *, modulo=None):
    original = sanitizar_entrada_scanner(valor)
    if not original:
        return ''
    if _entrada_e_leitura_numerica(original):
        somente_digitos = re.sub(r'\D', '', original)
        normalizado = codigo_bipagem_primario(original, modulo=modulo)
        if somente_digitos != normalizado:
            _log_codigo_normalizado(somente_digitos, normalizado, modulo)
        return normalizado
    return ''.join(original.split()).upper()


def _codigo_exibicao_produto(produto):
    return str(produto.cod_prod or produto.codigo or '').strip()


def _codigo_variantes(codigo):
    return variantes_codigo_barras(codigo)


def _identificadores_produto(produto):
    identificadores = set()
    for valor in [getattr(produto, 'cod_ean', ''), getattr(produto, 'cod_prod', ''), getattr(produto, 'codigo', '')]:
        if not valor:
            continue
        identificadores.update(_codigo_variantes(valor))
        if _entrada_e_leitura_numerica(valor):
            barras = normalizar_codigo_barras(valor)
            if barras:
                identificadores.update(_codigo_variantes(barras))
    return identificadores


def _codigo_corresponde_identificador(codigo_lido_normalizado, identificadores):
    if not codigo_lido_normalizado or not identificadores:
        return False
    for variante in _codigo_variantes(codigo_lido_normalizado):
        if variante in identificadores:
            return True
    return False


def _chave_cache_produto(variantes):
    if not variantes:
        return ''
    return f'wms:prod:lookup:{variantes[0]}'


def _buscar_produto_por_codigo(codigo_normalizado):
    if not codigo_normalizado:
        return None
    variantes = _codigo_variantes(codigo_normalizado)
    if not variantes:
        return None

    cache_key = _chave_cache_produto(variantes)
    produto_id = cache.get(cache_key)
    if produto_id:
        produto = (
            Produto.objects.filter(pk=produto_id, ativo=True)
            .only('id', 'cod_prod', 'cod_ean', 'codigo', 'setor', 'categoria', 'descricao')
            .first()
        )
        if produto:
            registrar_cache_hit('produto', cache_key)
            return produto
        cache.delete(cache_key)

    registrar_cache_miss('produto', cache_key)
    filtro = Q()
    for variante in variantes:
        filtro |= Q(cod_ean=variante) | Q(cod_prod=variante) | Q(codigo=variante)
    produto = (
        Produto.objects.filter(filtro, ativo=True)
        .only('id', 'cod_prod', 'cod_ean', 'codigo', 'setor', 'categoria', 'descricao')
        .order_by('id')
        .first()
    )
    if produto:
        cache.set(cache_key, produto.id, PRODUTO_LOOKUP_CACHE_TTL)
    return produto


def _log_validacao(
    usuario,
    tipo_validacao,
    codigo_lido,
    produto_encontrado_id,
    item_id,
    produto_esperado_id,
    produto_setor,
    item_setor,
    resultado,
):
    if usuario is None:
        return
    try:
        Log.objects.create(
            usuario=usuario,
            acao=f'VALIDACAO PRODUTO {tipo_validacao}',
            detalhe=(
                f'codigo_lido={codigo_lido}; produto_encontrado_id={produto_encontrado_id}; '
                f'item_id={item_id}; produto_esperado_id={produto_esperado_id}; '
                f'produto_setor={produto_setor}; item_setor={item_setor}; '
                f'usuario={getattr(usuario, "id", None)}; '
                f'timestamp={timezone.now().strftime("%Y-%m-%d %H:%M:%S")}; resultado={resultado}'
            ),
        )
    except OperationalError as exc:
        if not (connection.vendor == 'sqlite' and 'database is locked' in str(exc).lower()):
            raise
