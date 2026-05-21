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


def queryset_tarefa_operacional():
    """Queryset de Tarefa seguro para schema brownfield (sem colunas de onda quando ausentes)."""
    from apps.tarefas.models import Tarefa

    if coluna_tarefa_onda_id_disponivel():
        return Tarefa.objects
    return queryset_tarefa_legado()


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
