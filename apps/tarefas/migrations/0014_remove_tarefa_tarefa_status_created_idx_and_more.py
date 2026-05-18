from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tarefas', '0013_tarefa_operational_indexes'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='tarefa',
            name='tarefa_status_created_idx',
        ),
        migrations.RemoveIndex(
            model_name='tarefa',
            name='tarefa_ativo_status_upd_idx',
        ),
        migrations.RemoveIndex(
            model_name='tarefaitem',
            name='tarefa_item_tarefa_prod_idx',
        ),
    ]