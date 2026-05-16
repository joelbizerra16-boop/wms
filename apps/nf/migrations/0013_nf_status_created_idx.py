from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nf', '0012_notafiscal_bairro'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='notafiscal',
            index=models.Index(fields=['status', 'created_at'], name='nf_status_created_idx'),
        ),
    ]
