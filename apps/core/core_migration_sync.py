"""
Sincronizacao segura entre schema PostgreSQL legado e django_migrations (app core).

Nao apaga dados. Nao recria tabelas. Apenas registra migrations ja materializadas no banco.
"""

from __future__ import annotations

import logging

from django.db import connection
from django.db.migrations.recorder import MigrationRecorder


logger = logging.getLogger(__name__)

CORE_APP = 'core'

# Ordem topologica obrigatoria (dependencias Django).
CORE_MIGRATIONS_ORDEM = (
    '0001_minuta_models',
    '0002_minutaromaneioitem_bairro',
    '0003_minutaromaneio_importacao_lote',
    '0004_backfill_minuta_importacao_lote_legado',
    '0005_minuta_expedicao_persistencia',
    '0006_minutaromaneio_tipo_minuta_idx',
    '0007_reconcile_minuta_schema_postgresql',
    '0008_minutaromaneio_lote_created_idx',
)

COLUNAS_EXPEDICAO_ROMANEIO = (
    'hash_operacional',
    'status_expedicao',
    'tipo_minuta',
    'pdf_gerado_em',
)


def migrations_core_aplicadas(conn=None) -> set[str]:
    conn = conn or connection
    recorder = MigrationRecorder(conn)
    return {nome for app, nome in recorder.applied_migrations() if app == CORE_APP}


def _tabela_existe(cursor, nome_tabela: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = %s
        )
        """,
        [nome_tabela],
    )
    return bool(cursor.fetchone()[0])


def _coluna_existe(cursor, nome_tabela: str, nome_coluna: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s
              AND column_name = %s
        )
        """,
        [nome_tabela, nome_coluna],
    )
    return bool(cursor.fetchone()[0])


def _indice_existe(cursor, nome_indice: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND indexname = %s
        )
        """,
        [nome_indice],
    )
    return bool(cursor.fetchone()[0])


def migration_materializada_no_banco(cursor, nome_migration: str) -> bool:
    """True quando o efeito da migration ja existe fisicamente no PostgreSQL."""
    if nome_migration == '0001_minuta_models':
        return _tabela_existe(cursor, 'core_minutaromaneio') and _tabela_existe(cursor, 'core_minutaromaneioitem')

    if nome_migration == '0002_minutaromaneioitem_bairro':
        return _coluna_existe(cursor, 'core_minutaromaneioitem', 'bairro')

    if nome_migration == '0003_minutaromaneio_importacao_lote':
        return _coluna_existe(cursor, 'core_minutaromaneio', 'importacao_lote')

    if nome_migration == '0004_backfill_minuta_importacao_lote_legado':
        # RunPython de backfill: seguro marcar se lote ja existe (idempotente).
        return _coluna_existe(cursor, 'core_minutaromaneio', 'importacao_lote')

    if nome_migration in {'0005_minuta_expedicao_persistencia', '0006_minutaromaneio_tipo_minuta_idx'}:
        return all(_coluna_existe(cursor, 'core_minutaromaneio', coluna) for coluna in COLUNAS_EXPEDICAO_ROMANEIO)

    if nome_migration == '0007_reconcile_minuta_schema_postgresql':
        return all(_coluna_existe(cursor, 'core_minutaromaneio', coluna) for coluna in COLUNAS_EXPEDICAO_ROMANEIO[:3])

    if nome_migration == '0008_minutaromaneio_lote_created_idx':
        return _indice_existe(cursor, 'min_rom_lote_created_ix')

    return False


def diagnosticar_divergencia_migrations_core(conn=None) -> dict:
    """Compara django_migrations x objetos reais no banco."""
    conn = conn or connection
    aplicadas = migrations_core_aplicadas(conn)
    divergencias = []
    pendentes_reais = []

    tabela_romaneio = False
    if conn.vendor != 'postgresql':
        return {
            'vendor': conn.vendor,
            'aplicadas': sorted(aplicadas),
            'divergencias': [],
            'pendentes_reais': [],
            'tabela_romaneio_existe': False,
        }

    with conn.cursor() as cursor:
        tabela_romaneio = _tabela_existe(cursor, 'core_minutaromaneio')
        for nome in CORE_MIGRATIONS_ORDEM:
            materializada = migration_materializada_no_banco(cursor, nome)
            if nome in aplicadas:
                continue
            if materializada:
                pendentes_reais.append(nome)
            elif nome == '0001_minuta_models' and tabela_romaneio:
                divergencias.append(
                    'Tabela core_minutaromaneio existe mas 0001 nao esta em django_migrations '
                    '(bootstrap deve corrigir antes do migrate).'
                )

    return {
        'vendor': conn.vendor,
        'aplicadas': sorted(aplicadas),
        'divergencias': divergencias,
        'pendentes_reais': pendentes_reais,
        'tabela_romaneio_existe': tabela_romaneio,
    }


def registrar_migration_fake(conn, nome_migration: str) -> None:
    MigrationRecorder(conn).record_applied(CORE_APP, nome_migration)


def sincronizar_historico_migrations_core(conn=None) -> list[str]:
    """
    Registra em django_migrations as migrations core cujo efeito ja existe no banco.
    Retorna lista de migrations recem-registradas (fake seguro).
    """
    conn = conn or connection
    if conn.vendor != 'postgresql':
        logger.info('core_migration_sync: ignorado vendor=%s', conn.vendor)
        return []

    aplicadas = migrations_core_aplicadas(conn)
    registradas: list[str] = []

    with conn.cursor() as cursor:
        for nome in CORE_MIGRATIONS_ORDEM:
            if nome in aplicadas:
                continue
            if not migration_materializada_no_banco(cursor, nome):
                continue
            registrar_migration_fake(conn, nome)
            aplicadas.add(nome)
            registradas.append(nome)
            logger.info('core_migration_sync: fake registrado core.%s', nome)

    if registradas:
        logger.info('core_migration_sync: total_fake=%s migrations=%s', len(registradas), registradas)
    else:
        logger.info('core_migration_sync: nenhum fake necessario')

    return registradas
