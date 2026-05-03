from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("usuarios", "0002_remove_usuario_usuario_username_idx"),
        ("tarefas", "0008_tarefa_ativo"),
    ]

    operations = [
        migrations.AddField(
            model_name="tarefaitem",
            name="bipado_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="itens_bipados_separacao",
                to="usuarios.usuario",
                verbose_name="bipado por",
            ),
        ),
        migrations.AddField(
            model_name="tarefaitem",
            name="data_bipagem",
            field=models.DateTimeField(blank=True, null=True, verbose_name="data da bipagem"),
        ),
    ]
