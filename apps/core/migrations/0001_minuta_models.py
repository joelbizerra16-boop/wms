from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('nf', '0011_notafiscalitem_snapshot_xml_e_produto_null'),
    ]

    operations = [
        migrations.CreateModel(
            name='MinutaRomaneio',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='criado em')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='atualizado em')),
                ('codigo_romaneio', models.CharField(db_index=True, max_length=40, verbose_name='codigo do romaneio')),
                ('filial', models.CharField(blank=True, default='', max_length=255, verbose_name='filial')),
                ('data_saida', models.DateField(blank=True, db_index=True, null=True, verbose_name='data de saida')),
                ('destino', models.CharField(blank=True, default='', max_length=255, verbose_name='destino')),
                ('km', models.CharField(blank=True, default='', max_length=50, verbose_name='km')),
                ('rotas', models.CharField(blank=True, default='', max_length=255, verbose_name='rotas')),
                ('quantidade_pedidos', models.PositiveIntegerField(blank=True, null=True, verbose_name='quantidade de pedidos')),
                ('quantidade_clientes', models.PositiveIntegerField(blank=True, null=True, verbose_name='quantidade de clientes')),
                ('veiculo', models.CharField(blank=True, default='', max_length=255, verbose_name='veiculo')),
                ('placa', models.CharField(blank=True, db_index=True, default='', max_length=30, verbose_name='placa')),
                ('motorista', models.CharField(blank=True, default='', max_length=255, verbose_name='motorista')),
                ('ajudante_1', models.CharField(blank=True, default='', max_length=255, verbose_name='ajudante 1')),
                ('ajudante_2', models.CharField(blank=True, default='', max_length=255, verbose_name='ajudante 2')),
                ('ajudante_3', models.CharField(blank=True, default='', max_length=255, verbose_name='ajudante 3')),
                ('numero_box', models.CharField(blank=True, default='', max_length=50, verbose_name='numero do box')),
                ('transportadora', models.CharField(blank=True, default='', max_length=255, verbose_name='transportadora')),
                ('arquivo_nome', models.CharField(blank=True, default='', max_length=255, verbose_name='arquivo importado')),
                ('usuario_importacao', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='romaneios_minuta_importados', to=settings.AUTH_USER_MODEL, verbose_name='usuario responsavel pela importacao')),
            ],
            options={
                'verbose_name': 'romaneio da minuta',
                'verbose_name_plural': 'romaneios da minuta',
                'ordering': ('-data_saida', '-id'),
            },
        ),
        migrations.CreateModel(
            name='MinutaRomaneioItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='criado em')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='atualizado em')),
                ('numero_nota', models.CharField(db_index=True, max_length=20, verbose_name='numero da nota')),
                ('sequencia_entrega', models.CharField(blank=True, default='', max_length=20, verbose_name='sequencia da entrega')),
                ('codigo_cliente', models.CharField(blank=True, default='', max_length=50, verbose_name='codigo do cliente')),
                ('fantasia', models.CharField(blank=True, default='', max_length=255, verbose_name='fantasia')),
                ('razao_social', models.CharField(blank=True, default='', max_length=255, verbose_name='razao social')),
                ('numero_pedido', models.CharField(blank=True, default='', max_length=50, verbose_name='numero do pedido')),
                ('tipo_cobranca', models.CharField(blank=True, default='', max_length=100, verbose_name='tipo de cobranca')),
                ('peso_kg', models.DecimalField(decimal_places=3, default=0, max_digits=14, verbose_name='peso em kg')),
                ('volume_m3', models.DecimalField(decimal_places=3, default=0, max_digits=14, verbose_name='volume em m3')),
                ('valor_total', models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name='valor total')),
                ('status', models.CharField(blank=True, db_index=True, default='PENDENTE', max_length=40, verbose_name='status')),
                ('duplicado', models.BooleanField(db_index=True, default=False, verbose_name='duplicado')),
                ('duplicidade_romaneio_codigo', models.CharField(blank=True, default='', max_length=40, verbose_name='romaneio anterior')),
                ('duplicidade_data_saida', models.DateField(blank=True, null=True, verbose_name='data do romaneio anterior')),
                ('duplicidade_placa', models.CharField(blank=True, default='', max_length=30, verbose_name='placa anterior')),
                ('duplicidade_motorista', models.CharField(blank=True, default='', max_length=255, verbose_name='motorista anterior')),
                ('duplicidade_usuario', models.CharField(blank=True, default='', max_length=150, verbose_name='usuario anterior')),
                ('nf', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='itens_minuta_romaneio', to='nf.notafiscal', verbose_name='nota fiscal vinculada')),
                ('romaneio', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='itens', to='core.minutaromaneio', verbose_name='romaneio')),
            ],
            options={
                'verbose_name': 'item da minuta',
                'verbose_name_plural': 'itens da minuta',
                'ordering': ('-romaneio__data_saida', 'romaneio__codigo_romaneio', 'numero_nota'),
            },
        ),
        migrations.AddConstraint(
            model_name='minutaromaneio',
            constraint=models.UniqueConstraint(fields=('codigo_romaneio', 'data_saida'), name='min_rom_cod_data_uq'),
        ),
        migrations.AddConstraint(
            model_name='minutaromaneioitem',
            constraint=models.UniqueConstraint(fields=('romaneio', 'numero_nota'), name='min_item_rom_nf_uq'),
        ),
        migrations.AddIndex(
            model_name='minutaromaneio',
            index=models.Index(fields=['codigo_romaneio', 'data_saida'], name='min_rom_cod_data_ix'),
        ),
        migrations.AddIndex(
            model_name='minutaromaneioitem',
            index=models.Index(fields=['numero_nota'], name='min_item_nota_ix'),
        ),
        migrations.AddIndex(
            model_name='minutaromaneioitem',
            index=models.Index(fields=['duplicado', 'status'], name='min_item_dup_st_ix'),
        ),
    ]