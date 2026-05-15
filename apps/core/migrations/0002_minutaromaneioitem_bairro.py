from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_minuta_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='minutaromaneioitem',
            name='bairro',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='bairro'),
        ),
    ]