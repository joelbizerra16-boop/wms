from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('produtos', '0006_produto_codigo_setor_indexes'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='produto',
            index=models.Index(
                fields=['ativo', 'cod_ean'],
                name='produto_ativo_ean_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='produto',
            index=models.Index(
                fields=['ativo', 'cod_prod'],
                name='produto_ativo_cod_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='produto',
            index=models.Index(
                fields=['ativo', 'codigo'],
                name='produto_ativo_codigo_idx',
            ),
        ),
    ]
