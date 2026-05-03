from django.db import migrations, models


def migrar_status_normal_para_pendente(apps, schema_editor):
    NotaFiscal = apps.get_model('nf', 'NotaFiscal')
    NotaFiscal.objects.filter(status='NORMAL').update(status='PENDENTE')


class Migration(migrations.Migration):

    dependencies = [
        ('nf', '0003_notafiscal_status_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notafiscal',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDENTE', 'Pendente'),
                    ('EM_CONFERENCIA', 'Em conferencia'),
                    ('CONCLUIDO', 'Concluido'),
                    ('CONCLUIDO_COM_RESTRICAO', 'Concluido com restricao'),
                    ('NORMAL', 'Normal'),
                    ('BLOQUEADA_COM_RESTRICAO', 'Bloqueada com restricao'),
                    ('LIBERADA_COM_RESTRICAO', 'Liberada com restricao'),
                ],
                db_index=True,
                default='PENDENTE',
                max_length=30,
                verbose_name='status operacional',
            ),
        ),
        migrations.RunPython(migrar_status_normal_para_pendente, migrations.RunPython.noop),
    ]