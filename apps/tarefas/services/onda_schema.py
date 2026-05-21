"""Detecção de schema brownfield para módulo de ondas (rollout progressivo)."""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.db import connection

logger = logging.getLogger(__name__)

CACHE_KEY_SCHEMA_ONDA = 'wms:schema:onda_disponivel'
CACHE_KEY_COLUNA_ONDA_ID = 'wms:schema:tarefa_onda_id'
CACHE_TTL_SCHEMA_ONDA = 300

TAREFA_CAMPOS_LEGADO = (
    'id',
    'created_at',
    'updated_at',
    'tipo',
    'setor',
    'nf_id',
    'rota_id',
    'usuario_id',
    'usuario_em_execucao_id',
    'data_inicio',
    'status',
    'ativo',
)


def _tabela_existe(cursor, tabela: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = %s
        LIMIT 1
        """,
        [tabela],
    )
    return cursor.fetchone() is not None


def _coluna_existe(cursor, tabela: str, coluna: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
        LIMIT 1
        """,
        [tabela, coluna],
    )
    return cursor.fetchone() is not None


def _avaliar_schema_onda_no_banco() -> tuple[bool, bool, bool]:
    if connection.vendor != 'postgresql':
        return True, True, True

    with connection.cursor() as cursor:
        tabela_onda = _tabela_existe(cursor, 'tarefas_ondaseparacao')
        coluna_onda_id = _coluna_existe(cursor, 'tarefas_tarefa', 'onda_id')
    disponivel = tabela_onda and coluna_onda_id
    return disponivel, tabela_onda, coluna_onda_id


def coluna_tarefa_onda_id_disponivel(*, force_refresh: bool = False) -> bool:
    if not force_refresh:
        cached = cache.get(CACHE_KEY_COLUNA_ONDA_ID)
        if cached is not None:
            return bool(cached)

    if connection.vendor != 'postgresql':
        disponivel = True
    else:
        with connection.cursor() as cursor:
            disponivel = _coluna_existe(cursor, 'tarefas_tarefa', 'onda_id')

    cache.set(CACHE_KEY_COLUNA_ONDA_ID, disponivel, CACHE_TTL_SCHEMA_ONDA)
    return disponivel


def schema_onda_disponivel(*, force_refresh: bool = False) -> bool:
    if not force_refresh:
        cached = cache.get(CACHE_KEY_SCHEMA_ONDA)
        if cached is not None:
            return bool(cached)

    disponivel, tabela_onda, coluna_onda_id = _avaliar_schema_onda_no_banco()
    if not disponivel:
        logger.warning(
            'SCHEMA_ONDA_INDISPONIVEL modo=classico tabela_onda=%s coluna_onda_id=%s',
            tabela_onda,
            coluna_onda_id,
        )

    cache.set(CACHE_KEY_SCHEMA_ONDA, disponivel, CACHE_TTL_SCHEMA_ONDA)
    cache.set(CACHE_KEY_COLUNA_ONDA_ID, coluna_onda_id, CACHE_TTL_SCHEMA_ONDA)
    return disponivel


def invalidate_schema_onda_cache():
    cache.delete(CACHE_KEY_SCHEMA_ONDA)
    cache.delete(CACHE_KEY_COLUNA_ONDA_ID)


def queryset_tarefa_legado():
    from apps.tarefas.models import Tarefa

    return Tarefa.objects.only(*TAREFA_CAMPOS_LEGADO)


_BROWNFIELD_RUNTIME_TENTADO = False


def _tentar_alinhar_schema_onda_runtime():
    global _BROWNFIELD_RUNTIME_TENTADO
    if _BROWNFIELD_RUNTIME_TENTADO or connection.vendor != 'postgresql':
        return
    _BROWNFIELD_RUNTIME_TENTADO = True
    try:
        from apps.tarefas.db_onda_brownfield import aplicar_schema_onda_brownfield

        aplicar_schema_onda_brownfield(connection)
        invalidate_schema_onda_cache()
        logger.info('ONDA_BROWNFIELD_SCHEMA_APLICADO origem=runtime_auto')
    except Exception:
        logger.exception('ONDA_BROWNFIELD_SCHEMA_FALHA origem=runtime_auto')


def queryset_tarefa_operacional():
    """Queryset de Tarefa seguro para schema brownfield (sem colunas de onda quando ausentes)."""
    from apps.tarefas.models import Tarefa

    if not coluna_tarefa_onda_id_disponivel():
        _tentar_alinhar_schema_onda_runtime()
    if coluna_tarefa_onda_id_disponivel(force_refresh=True):
        return Tarefa.objects
    logger.info('SEPARACAO_QUERYSET_LEGADO contexto=operacional')
    return queryset_tarefa_legado()


def queryset_tarefa_web(*, prefetch_itens=False, prefetch_itens_nf=False):
    """Detalhe/impressão/execução web sem carregar onda_id quando coluna ausente."""
    if coluna_tarefa_onda_id_disponivel():
        qs = queryset_tarefa_operacional().select_related(
            'nf',
            'rota',
            'usuario',
            'usuario_em_execucao',
            'onda',
        )
    else:
        logger.info('SEPARACAO_QUERYSET_LEGADO contexto=web')
        qs = queryset_tarefa_legado().select_related('nf', 'rota', 'usuario', 'usuario_em_execucao')
    qs = qs.defer('nf__bairro')
    if prefetch_itens_nf:
        return qs.prefetch_related('itens__produto', 'itens__nf')
    if prefetch_itens:
        return qs.prefetch_related('itens__produto')
    return qs


def queryset_tarefa_lock(**select_for_update_kwargs):
    if not coluna_tarefa_onda_id_disponivel():
        logger.info('SEPARACAO_QUERYSET_LEGADO contexto=lock')
    return queryset_tarefa_operacional().select_for_update(**select_for_update_kwargs)


def campos_tarefa_bipagem_lock():
    if coluna_tarefa_onda_id_disponivel():
        return (
            'id',
            'status',
            'setor',
            'tipo',
            'nf_id',
            'rota_id',
            'usuario_id',
            'usuario_em_execucao_id',
            'itens_total',
            'itens_pendentes',
            'onda_id',
        )
    return TAREFA_CAMPOS_LEGADO


def queryset_tarefa_bipagem_lock(*, tarefa_id, select_for_update_kwargs):
    campos = campos_tarefa_bipagem_lock()
    if coluna_tarefa_onda_id_disponivel():
        return (
            queryset_tarefa_operacional()
            .select_related('onda')
            .select_for_update(**select_for_update_kwargs)
            .only(*campos)
            .get(id=tarefa_id)
        )
    logger.info('SEPARACAO_QUERYSET_LEGADO contexto=bipagem_lock')
    return queryset_tarefa_legado().select_for_update(**select_for_update_kwargs).get(id=tarefa_id)


def queryset_tarefa_item_com_tarefa(queryset=None):
    """select_related('tarefa') sem carregar onda_id quando coluna não existe."""
    from apps.tarefas.models import TarefaItem

    qs = queryset if queryset is not None else TarefaItem.objects.all()
    if coluna_tarefa_onda_id_disponivel():
        return qs.select_related('tarefa')
    campos_item = (
        'id',
        'created_at',
        'updated_at',
        'tarefa_id',
        'nf_id',
        'produto_id',
        'quantidade_total',
        'quantidade_separada',
        'possui_restricao',
        'bipado_por_id',
        'data_bipagem',
        'grupo_agregado_id',
    )
    campos_tarefa_rel = tuple(f'tarefa__{campo}' for campo in TAREFA_CAMPOS_LEGADO)
    return qs.select_related('tarefa').only(*campos_item, *campos_tarefa_rel)
