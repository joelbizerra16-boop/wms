from django.db import migrations


def reconciliar_schema_minuta_postgresql(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return

    with schema_editor.connection.cursor() as cursor:
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
        tabela_existe = cursor.fetchone()[0]
        if not tabela_existe:
            return

        comandos = [
            "ALTER TABLE core_minutaromaneio ADD COLUMN IF NOT EXISTS hash_operacional VARCHAR(64)",
            "ALTER TABLE core_minutaromaneio ADD COLUMN IF NOT EXISTS status_expedicao VARCHAR(20)",
            "ALTER TABLE core_minutaromaneio ADD COLUMN IF NOT EXISTS tipo_minuta VARCHAR(40)",
            "UPDATE core_minutaromaneio SET hash_operacional = '' WHERE hash_operacional IS NULL",
            "UPDATE core_minutaromaneio SET status_expedicao = 'ATIVA' WHERE status_expedicao IS NULL OR BTRIM(status_expedicao) = ''",
            "UPDATE core_minutaromaneio SET tipo_minuta = '' WHERE tipo_minuta IS NULL",
            "ALTER TABLE core_minutaromaneio ALTER COLUMN hash_operacional SET DEFAULT ''",
            "ALTER TABLE core_minutaromaneio ALTER COLUMN hash_operacional SET NOT NULL",
            "ALTER TABLE core_minutaromaneio ALTER COLUMN status_expedicao SET DEFAULT 'ATIVA'",
            "ALTER TABLE core_minutaromaneio ALTER COLUMN status_expedicao SET NOT NULL",
            "ALTER TABLE core_minutaromaneio ALTER COLUMN tipo_minuta SET DEFAULT ''",
            "ALTER TABLE core_minutaromaneio ALTER COLUMN tipo_minuta SET NOT NULL",
            "CREATE INDEX IF NOT EXISTS min_rom_hash_operacional_ix ON core_minutaromaneio (hash_operacional)",
            "CREATE INDEX IF NOT EXISTS min_rom_status_expedicao_ix ON core_minutaromaneio (status_expedicao)",
            "CREATE INDEX IF NOT EXISTS min_rom_tipo_minuta_ix ON core_minutaromaneio (tipo_minuta)",
            "CREATE INDEX IF NOT EXISTS min_rom_exp_pdf_ix ON core_minutaromaneio (status_expedicao, pdf_gerado_em)",
        ]
        for comando in comandos:
            cursor.execute(comando)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_minutaromaneio_tipo_minuta_idx'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(reconciliar_schema_minuta_postgresql, migrations.RunPython.noop),
            ],
            state_operations=[],
        ),
    ]