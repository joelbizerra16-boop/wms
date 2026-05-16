import logging
from threading import Lock
from uuid import uuid4


logger = logging.getLogger(__name__)

_SCHEMA_FIX_LOCK = Lock()
_SCHEMA_FIX_DONE = set()


def invalidar_cache_schema_fix():
    _SCHEMA_FIX_DONE.clear()


def _invalidar_cache_colunas_nota_fiscal():
    from apps.nf.models import invalidar_cache_colunas_nota_fiscal

    invalidar_cache_colunas_nota_fiscal()


def _tabela_existe(cursor, table_name):
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_name = %s
        """,
        [table_name],
    )
    return cursor.fetchone() is not None


def _coluna_existe(cursor, table_name, column_name):
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = %s
          AND column_name = %s
        """,
        [table_name, column_name],
    )
    return cursor.fetchone() is not None


def garantir_estrutura_minuta(connection):
    if connection.vendor != 'postgresql':
        return False

    marcador = (connection.alias, 'core', 'minuta_schema')
    with _SCHEMA_FIX_LOCK:
        if marcador in _SCHEMA_FIX_DONE:
            return False

        try:
            with connection.cursor() as cursor:
                tabela_romaneio_existe = _tabela_existe(cursor, 'core_minutaromaneio')
                tabela_item_existe = _tabela_existe(cursor, 'core_minutaromaneioitem')

            alterado = False
            if not tabela_romaneio_existe or not tabela_item_existe:
                from apps.core.models import MinutaRomaneio, MinutaRomaneioItem

                with connection.schema_editor() as schema_editor:
                    if not tabela_romaneio_existe:
                        schema_editor.create_model(MinutaRomaneio)
                        logger.warning('AUTO_FIX_SCHEMA: tabela core_minutaromaneio criada automaticamente.')
                        alterado = True
                    if not tabela_item_existe:
                        schema_editor.create_model(MinutaRomaneioItem)
                        logger.warning('AUTO_FIX_SCHEMA: tabela core_minutaromaneioitem criada automaticamente.')
                        alterado = True

            with connection.cursor() as cursor:
                if _tabela_existe(cursor, 'core_minutaromaneio') and not _coluna_existe(cursor, 'core_minutaromaneio', 'importacao_lote'):
                    cursor.execute(
                        'ALTER TABLE "core_minutaromaneio" ADD COLUMN IF NOT EXISTS "importacao_lote" UUID'
                    )
                    cursor.execute(
                        'CREATE INDEX IF NOT EXISTS "core_minutaromaneio_importacao_lote_9f0b4d9d" ON "core_minutaromaneio" ("importacao_lote")'
                    )
                    cursor.execute(
                        'SELECT id FROM "core_minutaromaneio" WHERE "importacao_lote" IS NULL'
                    )
                    romaneio_ids = [linha[0] for linha in cursor.fetchall()]
                    if romaneio_ids:
                        cursor.executemany(
                            'UPDATE "core_minutaromaneio" SET "importacao_lote" = %s WHERE "id" = %s',
                            [(str(uuid4()), romaneio_id) for romaneio_id in romaneio_ids],
                        )
                    logger.warning('AUTO_FIX_SCHEMA: coluna core_minutaromaneio.importacao_lote criada automaticamente.')
                    alterado = True

                if _tabela_existe(cursor, 'core_minutaromaneioitem') and not _coluna_existe(cursor, 'core_minutaromaneioitem', 'bairro'):
                    cursor.execute(
                        "ALTER TABLE \"core_minutaromaneioitem\" ADD COLUMN IF NOT EXISTS \"bairro\" VARCHAR(100) DEFAULT ''"
                    )
                    logger.warning('AUTO_FIX_SCHEMA: coluna core_minutaromaneioitem.bairro criada automaticamente.')
                    alterado = True

            _SCHEMA_FIX_DONE.add(marcador)
            return alterado
        except Exception:
            logger.exception('AUTO_FIX_SCHEMA: falha ao garantir estrutura da minuta.')
            return False


def garantir_coluna_bairro(connection):
    if connection.vendor != 'postgresql':
        return False

    marcador = (connection.alias, 'nf_notafiscal', 'bairro')
    with _SCHEMA_FIX_LOCK:
        if marcador in _SCHEMA_FIX_DONE:
            return False

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND table_name = %s
                    """,
                    ['nf_notafiscal'],
                )
                if cursor.fetchone() is None:
                    return False

                cursor.execute(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = %s
                      AND column_name = %s
                    """,
                    ['nf_notafiscal', 'bairro'],
                )
                if cursor.fetchone() is None:
                    cursor.execute(
                        'ALTER TABLE "nf_notafiscal" ADD COLUMN IF NOT EXISTS "bairro" VARCHAR(100)'
                    )
                    cursor.execute(
                        'CREATE INDEX IF NOT EXISTS "nf_bairro_idx" ON "nf_notafiscal" ("bairro")'
                    )
                    logger.warning('AUTO_FIX_SCHEMA: coluna nf_notafiscal.bairro criada automaticamente.')
                    try:
                        _invalidar_cache_colunas_nota_fiscal()
                    except Exception:
                        logger.exception('AUTO_FIX_SCHEMA: falha ao invalidar cache de colunas da NotaFiscal.')

                _SCHEMA_FIX_DONE.add(marcador)
                return True
        except Exception:
            logger.exception('AUTO_FIX_SCHEMA: falha ao garantir coluna nf_notafiscal.bairro.')
            return False


def aplicar_correcoes_criticas(connection):
    corrigiu_bairro = garantir_coluna_bairro(connection)
    corrigiu_minuta = garantir_estrutura_minuta(connection)
    return corrigiu_bairro or corrigiu_minuta