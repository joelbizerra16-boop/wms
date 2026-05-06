from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('produtos', '0005_grupoagregado_produto_grupos_agregados'),
        ('nf', '0010_entradanf_xml_backup_gzip'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notafiscalitem',
            name='produto',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='itens_nota_fiscal', to='produtos.produto', verbose_name='produto'),
        ),
        migrations.AddField(
            model_name='notafiscalitem',
            name='cod_ean_xml',
            field=models.CharField(blank=True, default='', max_length=50, verbose_name='codigo EAN no XML'),
        ),
        migrations.AddField(
            model_name='notafiscalitem',
            name='cod_prod_xml',
            field=models.CharField(blank=True, default='', max_length=50, verbose_name='codigo do produto no XML'),
        ),
        migrations.AddField(
            model_name='notafiscalitem',
            name='descricao_xml',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='descricao do produto no XML'),
        ),
    ]