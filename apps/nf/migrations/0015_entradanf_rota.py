from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nf', '0014_remove_notafiscal_nf_status_created_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='entradanf',
            name='rota',
            field=models.CharField(
                blank=True,
                max_length=100,
                null=True,
                verbose_name='rota extraida do XML',
            ),
        ),
    ]
