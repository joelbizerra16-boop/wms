from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tarefas", "0009_tarefaitem_rastreabilidade_bipagem"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="tarefa",
            name="data_inicio",
            field=models.DateTimeField(blank=True, null=True, verbose_name="data inicio execucao"),
        ),
        migrations.AddField(
            model_name="tarefa",
            name="usuario_em_execucao",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tarefas_em_execucao",
                to=settings.AUTH_USER_MODEL,
                verbose_name="usuario em execucao",
            ),
        ),
        migrations.AddIndex(
            model_name="tarefa",
            index=models.Index(fields=["usuario_em_execucao", "status"], name="tarefa_execucao_status_idx"),
        ),
    ]
