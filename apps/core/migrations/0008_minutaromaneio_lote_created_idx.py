from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_reconcile_minuta_schema_postgresql'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='minutaromaneio',
            index=models.Index(fields=['importacao_lote', 'created_at'], name='min_rom_lote_created_ix'),
        ),
    ]
