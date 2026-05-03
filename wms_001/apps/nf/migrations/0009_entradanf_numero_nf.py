from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nf', '0008_entradanf'),
    ]

    operations = [
        migrations.AddField(
            model_name='entradanf',
            name='numero_nf',
            field=models.CharField(blank=True, db_index=True, default='', max_length=20, verbose_name='numero NF'),
        ),
    ]
