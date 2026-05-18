from django.db import migrations


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_minutaromaneio_tipo_minuta_idx'),
    ]

    operations = [
        migrations.RunPython(noop, noop),
    ]
