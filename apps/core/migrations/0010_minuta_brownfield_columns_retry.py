"""
Reaplica SQL brownfield se 0009 foi registrada sem efeito (execute multi-statement no psycopg2).
Idempotente: ADD COLUMN IF NOT EXISTS.
"""

from django.db import migrations

from apps.core.db_minuta_brownfield import aplicar_schema_minuta_brownfield


def reaplicar_colunas_minuta_brownfield(apps, schema_editor):
    aplicar_schema_minuta_brownfield(schema_editor.connection)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_minuta_brownfield_columns_postgresql'),
    ]

    operations = [
        migrations.RunPython(reaplicar_colunas_minuta_brownfield, migrations.RunPython.noop),
    ]
