"""
Colunas/tabela de onda ausentes em bancos brownfield (código deployado antes da migration 0015).
"""

from django.db import migrations

from apps.tarefas.db_onda_brownfield import aplicar_schema_onda_brownfield


def aplicar_colunas_onda_brownfield(apps, schema_editor):
    aplicar_schema_onda_brownfield(schema_editor.connection)


class Migration(migrations.Migration):

    dependencies = [
        ('tarefas', '0015_ondaseparacao_tarefa_wave_fields'),
    ]

    operations = [
        migrations.RunPython(aplicar_colunas_onda_brownfield, migrations.RunPython.noop),
    ]
