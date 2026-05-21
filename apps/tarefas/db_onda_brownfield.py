"""SQL idempotente para alinhar schema legado da onda no PostgreSQL."""

ONDA_BROWNFIELD_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS tarefas_ondaseparacao (
        id bigserial PRIMARY KEY,
        created_at timestamptz NOT NULL DEFAULT NOW(),
        updated_at timestamptz NOT NULL DEFAULT NOW(),
        codigo varchar(20) NOT NULL DEFAULT '',
        setor varchar(20) NOT NULL DEFAULT '',
        tipo_embalagem varchar(20) NOT NULL DEFAULT '',
        status varchar(30) NOT NULL DEFAULT 'PENDENTE',
        nf_total smallint NOT NULL DEFAULT 0,
        itens_total numeric(12, 2) NOT NULL DEFAULT 0,
        itens_bipados numeric(12, 2) NOT NULL DEFAULT 0,
        itens_pendentes numeric(12, 2) NOT NULL DEFAULT 0,
        percentual numeric(6, 2) NOT NULL DEFAULT 0,
        operador_id bigint NULL,
        rota_id bigint NOT NULL
    )
    """,
    "ALTER TABLE tarefas_tarefa ADD COLUMN IF NOT EXISTS onda_id bigint NULL",
    "ALTER TABLE tarefas_tarefa ADD COLUMN IF NOT EXISTS tipo_embalagem varchar(20) NOT NULL DEFAULT ''",
    "ALTER TABLE tarefas_tarefa ADD COLUMN IF NOT EXISTS ordem_na_onda smallint NOT NULL DEFAULT 1",
    "ALTER TABLE tarefas_tarefa ADD COLUMN IF NOT EXISTS itens_total numeric(12, 2) NOT NULL DEFAULT 0",
    "ALTER TABLE tarefas_tarefa ADD COLUMN IF NOT EXISTS itens_bipados numeric(12, 2) NOT NULL DEFAULT 0",
    "ALTER TABLE tarefas_tarefa ADD COLUMN IF NOT EXISTS itens_pendentes numeric(12, 2) NOT NULL DEFAULT 0",
    "ALTER TABLE tarefas_tarefa ADD COLUMN IF NOT EXISTS percentual numeric(6, 2) NOT NULL DEFAULT 0",
    """
    CREATE TABLE IF NOT EXISTS tarefas_ondaseparacao_nfs (
        id bigserial PRIMARY KEY,
        ondaseparacao_id bigint NOT NULL,
        notafiscal_id bigint NOT NULL,
        UNIQUE (ondaseparacao_id, notafiscal_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS onda_status_setor_idx ON tarefas_ondaseparacao (status, setor)",
    "CREATE INDEX IF NOT EXISTS onda_rota_setor_emb_idx ON tarefas_ondaseparacao (rota_id, setor, tipo_embalagem)",
    "CREATE INDEX IF NOT EXISTS tarefa_onda_status_idx ON tarefas_tarefa (onda_id, status)",
)


def aplicar_schema_onda_brownfield(connection):
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cursor:
        for sql in ONDA_BROWNFIELD_STATEMENTS:
            cursor.execute(sql)
