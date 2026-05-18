from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_minuta_expedicao_persistencia'),
    ]

    operations = [
        migrations.AlterField(
            model_name='minutaromaneio',
            name='tipo_minuta',
            field=models.CharField(blank=True, db_index=True, default='', max_length=40, verbose_name='tipo da minuta'),
        ),
    ]
