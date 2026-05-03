from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tarefas", "0007_tarefa_usuario_em_execucao"),
    ]

    operations = [
        migrations.AddField(
            model_name="tarefa",
            name="ativo",
            field=models.BooleanField(db_index=True, default=True, verbose_name="ativo"),
        ),
    ]
