"""
Colunas da minuta ausentes em bancos brownfield (tabela criada antes das migrations 0003-0005).

Cada ALTER em statement separado (psycopg2 nao executa multiplos comandos em um execute).
"""

from django.db import migrations

from apps.core.db_minuta_brownfield import aplicar_schema_minuta_brownfield


def aplicar_colunas_minuta_brownfield(apps, schema_editor):
    aplicar_schema_minuta_brownfield(schema_editor.connection)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_minutaromaneio_lote_created_idx'),
    ]

    operations = [
        migrations.RunPython(aplicar_colunas_minuta_brownfield, migrations.RunPython.noop),
    ]
