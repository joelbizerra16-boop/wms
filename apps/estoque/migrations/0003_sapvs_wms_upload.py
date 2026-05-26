# Generated manually for SAP vs WMS conciliation

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('estoque', '0002_movimentacao_estoque'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SapVsWmsUpload',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='criado em')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='atualizado em')),
                ('codigo_produto', models.CharField(db_index=True, max_length=50, verbose_name='código produto')),
                ('descricao', models.CharField(max_length=255, verbose_name='descrição')),
                ('quantidade_sap', models.DecimalField(decimal_places=2, max_digits=14, verbose_name='quantidade SAP')),
                ('setor', models.CharField(blank=True, db_index=True, default='', max_length=50, verbose_name='setor')),
                (
                    'usuario_upload',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='sap_vs_wms_uploads',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='usuário upload',
                    ),
                ),
            ],
            options={
                'verbose_name': 'upload SAP vs WMS',
                'verbose_name_plural': 'uploads SAP vs WMS',
                'ordering': ('codigo_produto',),
                'indexes': [
                    models.Index(fields=['codigo_produto'], name='sap_wms_cod_prod_ix'),
                    models.Index(fields=['setor'], name='sap_wms_setor_ix'),
                    models.Index(fields=['created_at'], name='sap_wms_created_ix'),
                ],
            },
        ),
    ]
