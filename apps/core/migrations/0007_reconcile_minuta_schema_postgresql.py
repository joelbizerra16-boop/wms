from django.db import migrations

from apps.core.db_fixes import aplicar_reconcile_schema_minuta_postgresql


def reconciliar_schema_minuta_postgresql(apps, schema_editor):
    aplicar_reconcile_schema_minuta_postgresql(schema_editor.connection)


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