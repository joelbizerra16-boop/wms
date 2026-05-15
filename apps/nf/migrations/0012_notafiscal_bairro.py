from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nf', '0011_notafiscalitem_snapshot_xml_e_produto_null'),
    ]

    operations = [
        migrations.AddField(
            model_name='notafiscal',
            name='bairro',
            field=models.CharField(blank=True, db_index=True, default='', max_length=100, verbose_name='bairro da NF'),
        ),
        migrations.AddIndex(
            model_name='notafiscal',
            index=models.Index(fields=['bairro'], name='nf_bairro_idx'),
        ),
    ]