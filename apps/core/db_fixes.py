import logging
from threading import Lock


logger = logging.getLogger(__name__)

_SCHEMA_VALIDATION_LOCK = Lock()
_SCHEMA_VALIDATION_CACHE = {}

MINUTA_SCHEMA_REQUERIDO = {
    'core_minutaromaneio': {
        'id',
        'created_at',
        'updated_at',
        'codigo_romaneio',
        'importacao_lote',
        'data_saida',
        'placa',
        'motorista',
        'usuario_importacao_id',
        'pdf_gerado_em',
        'pdf_gerado_por_id',
        'tipo_minuta',
        'hash_operacional',
        'status_expedicao',
    },
    'core_minutaromaneioitem': {
        'id',
        'created_at',
        'updated_at',
        'romaneio_id',
        'nf_id',
        'numero_nota',
        'fantasia',
        'razao_social',
        'bairro',
        'status',
        'duplicado',
        'duplicidade_romaneio_codigo',
        'duplicidade_data_saida',
        'duplicidade_motorista',
        'duplicidade_usuario',
        'peso_kg',
        'valor_total',
    },
}


def invalidar_cache_schema_fix():
    with _SCHEMA_VALIDATION_LOCK:
        _SCHEMA_VALIDATION_CACHE.clear()


def aplicar_reconcile_schema_minuta_postgresql(connection):
    """
    Idempotente: cria colunas/indice da minuta ausentes em banco legado.
    Executado em todo deploy antes do migrate (Render/Supabase brownfield).
    """
    if connection.vendor != 'postgresql':
        return False

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name = 'core_minutaromaneio'
            )
            """
        )
        if not cursor.fetchone()[0]:
            return False

        comandos = [
            "ALTER TABLE core_minutaromaneio ADD COLUMN IF NOT EXISTS importacao_lote UUID",
            "UPDATE core_minutaromaneio SET importacao_lote = gen_random_uuid() WHERE importacao_lote IS NULL",
            "ALTER TABLE core_minutaromaneio ADD COLUMN IF NOT EXISTS pdf_gerado_em TIMESTAMPTZ",
            "ALTER TABLE core_minutaromaneio ADD COLUMN IF NOT EXISTS pdf_gerado_por_id BIGINT",
            "ALTER TABLE core_minutaromaneio ADD COLUMN IF NOT EXISTS hash_operacional VARCHAR(64)",
            "ALTER TABLE core_minutaromaneio ADD COLUMN IF NOT EXISTS status_expedicao VARCHAR(20)",
            "ALTER TABLE core_minutaromaneio ADD COLUMN IF NOT EXISTS tipo_minuta VARCHAR(40)",
            "ALTER TABLE core_minutaromaneioitem ADD COLUMN IF NOT EXISTS bairro VARCHAR(100)",
            "UPDATE core_minutaromaneio SET hash_operacional = '' WHERE hash_operacional IS NULL",
            "UPDATE core_minutaromaneio SET status_expedicao = 'ATIVA' WHERE status_expedicao IS NULL OR BTRIM(status_expedicao) = ''",
            "UPDATE core_minutaromaneio SET tipo_minuta = '' WHERE tipo_minuta IS NULL",
            "ALTER TABLE core_minutaromaneio ALTER COLUMN hash_operacional SET DEFAULT ''",
            "ALTER TABLE core_minutaromaneio ALTER COLUMN hash_operacional SET NOT NULL",
            "ALTER TABLE core_minutaromaneio ALTER COLUMN status_expedicao SET DEFAULT 'ATIVA'",
            "ALTER TABLE core_minutaromaneio ALTER COLUMN status_expedicao SET NOT NULL",
            "ALTER TABLE core_minutaromaneio ALTER COLUMN tipo_minuta SET DEFAULT ''",
            "ALTER TABLE core_minutaromaneio ALTER COLUMN tipo_minuta SET NOT NULL",
            "CREATE INDEX IF NOT EXISTS min_rom_created_ix ON core_minutaromaneio (created_at)",
            "CREATE INDEX IF NOT EXISTS min_rom_hash_operacional_ix ON core_minutaromaneio (hash_operacional)",
            "CREATE INDEX IF NOT EXISTS min_rom_status_expedicao_ix ON core_minutaromaneio (status_expedicao)",
            "CREATE INDEX IF NOT EXISTS min_rom_tipo_minuta_ix ON core_minutaromaneio (tipo_minuta)",
            "CREATE INDEX IF NOT EXISTS min_rom_exp_pdf_ix ON core_minutaromaneio (status_expedicao, pdf_gerado_em)",
            "CREATE INDEX IF NOT EXISTS min_rom_lote_created_ix ON core_minutaromaneio (importacao_lote, created_at)",
        ]
        for comando in comandos:
            cursor.execute(comando)

    invalidar_cache_schema_fix()
    return True


def _cache_key(connection, escopo):
    return connection.alias, connection.vendor, escopo


def _obter_colunas_tabela(connection, table_name):
    with connection.cursor() as cursor:
        descricao = connection.introspection.get_table_description(cursor, table_name)
    return {coluna.name for coluna in descricao}


def diagnosticar_schema_minuta(connection):
    cache_key = _cache_key(connection, 'minuta_schema')
    with _SCHEMA_VALIDATION_LOCK:
        cached = _SCHEMA_VALIDATION_CACHE.get(cache_key)
        if cached is not None:
            return cached

    diagnostico = {
        'schema_detectado': connection.vendor,
        'alias': connection.alias,
        'tabelas_encontradas': [],
        'tabelas_faltantes': [],
        'colunas_faltantes': {},
        'erro': '',
        'resultado_validacao': False,
    }
    try:
        tabelas = set(connection.introspection.table_names())
        diagnostico['tabelas_encontradas'] = sorted(tabelas.intersection(MINUTA_SCHEMA_REQUERIDO))
        diagnostico['tabelas_faltantes'] = sorted(set(MINUTA_SCHEMA_REQUERIDO) - tabelas)
        for tabela, colunas_requeridas in MINUTA_SCHEMA_REQUERIDO.items():
            if tabela not in tabelas:
                continue
            colunas_atuais = _obter_colunas_tabela(connection, tabela)
            faltantes = sorted(colunas_requeridas - colunas_atuais)
            if faltantes:
                diagnostico['colunas_faltantes'][tabela] = faltantes
        diagnostico['resultado_validacao'] = not diagnostico['tabelas_faltantes'] and not diagnostico['colunas_faltantes']
    except Exception as exc:
        diagnostico['erro'] = str(exc)
        logger.exception('MINUTA_SCHEMA_CHECK falha ao validar schema da minuta.')

    with _SCHEMA_VALIDATION_LOCK:
        _SCHEMA_VALIDATION_CACHE[cache_key] = diagnostico
    return diagnostico


def mensagem_schema_minuta_inconsistente(diagnostico):
    if diagnostico.get('erro'):
        return (
            'Schema da minuta inconsistente. Execute python manage.py migrate e valide as migrations '
            '0005_minuta_expedicao_persistencia e 0007_reconcile_minuta_schema_postgresql.'
        )
    detalhes = []
    if diagnostico.get('tabelas_faltantes'):
        detalhes.append(f"tabelas_faltantes={diagnostico.get('tabelas_faltantes')}")
    if diagnostico.get('colunas_faltantes'):
        detalhes.append(f"colunas_faltantes={diagnostico.get('colunas_faltantes')}")
    sufixo = f" ({'; '.join(detalhes)})" if detalhes else ''
    return (
        'Schema da minuta inconsistente. Execute python manage.py migrate e valide as migrations '
        f'0005_minuta_expedicao_persistencia e 0007_reconcile_minuta_schema_postgresql.{sufixo}'
    )
