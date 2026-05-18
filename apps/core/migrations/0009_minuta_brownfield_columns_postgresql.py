"""
Colunas da minuta ausentes em bancos brownfield (tabela criada antes das migrations 0003-0005).

Aplica somente ADD COLUMN IF NOT EXISTS e indices IF NOT EXISTS no PostgreSQL.
Nao recria tabelas, nao apaga dados.
"""

from django.db import migrations


SQL_FORWARD = """
ALTER TABLE core_minutaromaneio
    ADD COLUMN IF NOT EXISTS importacao_lote uuid;

ALTER TABLE core_minutaromaneio
    ADD COLUMN IF NOT EXISTS hash_operacional varchar(64) NOT NULL DEFAULT '';

ALTER TABLE core_minutaromaneio
    ADD COLUMN IF NOT EXISTS pdf_gerado_em timestamp with time zone;

ALTER TABLE core_minutaromaneio
    ADD COLUMN IF NOT EXISTS pdf_gerado_por_id bigint;

ALTER TABLE core_minutaromaneio
    ADD COLUMN IF NOT EXISTS status_expedicao varchar(20) NOT NULL DEFAULT 'ATIVA';

ALTER TABLE core_minutaromaneio
    ADD COLUMN IF NOT EXISTS tipo_minuta varchar(40) NOT NULL DEFAULT '';

UPDATE core_minutaromaneio
SET importacao_lote = gen_random_uuid()
WHERE importacao_lote IS NULL;

UPDATE core_minutaromaneio
SET status_expedicao = 'ATIVA'
WHERE status_expedicao IS NULL OR status_expedicao = '';

UPDATE core_minutaromaneio
SET hash_operacional = ''
WHERE hash_operacional IS NULL;

UPDATE core_minutaromaneio
SET tipo_minuta = ''
WHERE tipo_minuta IS NULL;

ALTER TABLE core_minutaromaneioitem
    ADD COLUMN IF NOT EXISTS bairro varchar(100) NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS min_rom_created_ix
    ON core_minutaromaneio (created_at);

CREATE INDEX IF NOT EXISTS min_rom_exp_pdf_ix
    ON core_minutaromaneio (status_expedicao, pdf_gerado_em);

CREATE INDEX IF NOT EXISTS min_rom_lote_created_ix
    ON core_minutaromaneio (importacao_lote, created_at);

CREATE INDEX IF NOT EXISTS min_rom_tipo_minuta_ix
    ON core_minutaromaneio (tipo_minuta);
"""


def aplicar_colunas_minuta_brownfield(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(SQL_FORWARD)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_minutaromaneio_lote_created_idx'),
    ]

    operations = [
        migrations.RunPython(aplicar_colunas_minuta_brownfield, migrations.RunPython.noop),
    ]
