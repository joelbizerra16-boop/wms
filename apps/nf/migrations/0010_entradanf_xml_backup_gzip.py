from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nf', '0009_entradanf_numero_nf'),
    ]

    operations = [
        migrations.AddField(
            model_name='entradanf',
            name='xml_backup_gzip',
            field=models.BinaryField(blank=True, editable=False, null=True, verbose_name='backup XML compactado'),
        ),
    ]