from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tarefas', '0011_tarefaitem_grupo_agregado'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='tarefaitem',
            index=models.Index(
                fields=['tarefa', 'quantidade_separada'],
                name='tarefa_item_tarefa_sep_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='tarefa',
            index=models.Index(
                fields=['ativo', 'setor', 'status'],
                name='tarefa_ativo_setor_status_idx',
            ),
        ),
    ]
