from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tarefas', '0012_performance_indexes'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='tarefa',
            index=models.Index(fields=['status', 'created_at'], name='tarefa_status_created_idx'),
        ),
        migrations.AddIndex(
            model_name='tarefa',
            index=models.Index(fields=['ativo', 'status', 'updated_at'], name='tarefa_ativo_status_upd_idx'),
        ),
        migrations.AddIndex(
            model_name='tarefaitem',
            index=models.Index(fields=['tarefa', 'produto'], name='tarefa_item_tarefa_prod_idx'),
        ),
    ]
