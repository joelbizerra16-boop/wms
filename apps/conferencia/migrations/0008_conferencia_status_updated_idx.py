from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conferencia', '0007_remove_conferencia_nf_em_andamento_unique'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='conferencia',
            index=models.Index(fields=['status', 'updated_at'], name='conf_status_updated_idx'),
        ),
    ]
