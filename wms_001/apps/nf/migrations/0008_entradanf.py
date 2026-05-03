from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nf', '0007_notafiscal_status_inconsistente'),
    ]

    operations = [
        migrations.CreateModel(
            name='EntradaNF',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='criado em')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='atualizado em')),
                ('chave_nf', models.CharField(db_index=True, max_length=44, unique=True, verbose_name='chave NF')),
                ('xml', models.FileField(upload_to='xmls/', verbose_name='arquivo XML')),
                ('status', models.CharField(choices=[('AGUARDANDO', 'Aguardando'), ('PROCESSADO', 'Processado'), ('LIBERADO', 'Liberado')], db_index=True, default='AGUARDANDO', max_length=20)),
                ('tipo', models.CharField(choices=[('BALCAO', 'Balcao'), ('NORMAL', 'Normal')], db_index=True, default='NORMAL', max_length=20)),
                ('data_importacao', models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                'verbose_name': 'entrada de NF',
                'verbose_name_plural': 'entradas de NF',
                'ordering': ('-data_importacao', '-id'),
            },
        ),
        migrations.AddIndex(
            model_name='entradanf',
            index=models.Index(fields=['status', 'data_importacao'], name='entrada_nf_status_data_idx'),
        ),
    ]
