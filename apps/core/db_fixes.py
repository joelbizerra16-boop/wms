import logging
from threading import Lock


logger = logging.getLogger(__name__)

_SCHEMA_FIX_LOCK = Lock()
_SCHEMA_FIX_DONE = set()


def invalidar_cache_schema_fix():
    _SCHEMA_FIX_DONE.clear()


def _invalidar_cache_colunas_nota_fiscal():
    from apps.nf.models import invalidar_cache_colunas_nota_fiscal

    invalidar_cache_colunas_nota_fiscal()


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
    return garantir_coluna_bairro(connection)