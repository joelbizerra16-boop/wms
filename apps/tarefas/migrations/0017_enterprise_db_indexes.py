from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tarefas', '0016_onda_brownfield_postgresql'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='tarefaitem',
            index=models.Index(
                fields=['tarefa', 'produto', 'quantidade_separada'],
                name='tarefa_item_bipagem_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='tarefa',
            index=models.Index(
                fields=['status', 'usuario_em_execucao', 'ativo'],
                name='tarefa_status_exec_ativo_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='tarefa',
            index=models.Index(
                fields=['ativo', 'status', 'created_at'],
                name='tarefa_fila_operacional_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='ondaseparacao',
            index=models.Index(
                fields=['status', 'rota', 'setor'],
                name='onda_fila_operacional_idx',
            ),
        ),
    ]
