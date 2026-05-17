from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0004_backfill_minuta_importacao_lote_legado'),
    ]

    operations = [
        migrations.AddField(
            model_name='minutaromaneio',
            name='hash_operacional',
            field=models.CharField(blank=True, db_index=True, default='', max_length=64, verbose_name='hash operacional'),
        ),
        migrations.AddField(
            model_name='minutaromaneio',
            name='pdf_gerado_em',
            field=models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='pdf gerado em'),
        ),
        migrations.AddField(
            model_name='minutaromaneio',
            name='pdf_gerado_por',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='romaneios_minuta_pdf_gerados',
                to=settings.AUTH_USER_MODEL,
                verbose_name='usuario que gerou o pdf',
            ),
        ),
        migrations.AddField(
            model_name='minutaromaneio',
            name='status_expedicao',
            field=models.CharField(
                choices=[('ATIVA', 'Ativa'), ('IMPRESSA', 'Impressa')],
                db_index=True,
                default='ATIVA',
                max_length=20,
                verbose_name='status da expedicao',
            ),
        ),
        migrations.AddField(
            model_name='minutaromaneio',
            name='tipo_minuta',
            field=models.CharField(blank=True, default='', max_length=40, verbose_name='tipo da minuta'),
        ),
        migrations.AddIndex(
            model_name='minutaromaneio',
            index=models.Index(fields=['created_at'], name='min_rom_created_ix'),
        ),
        migrations.AddIndex(
            model_name='minutaromaneio',
            index=models.Index(fields=['status_expedicao', 'pdf_gerado_em'], name='min_rom_exp_pdf_ix'),
        ),
    ]
