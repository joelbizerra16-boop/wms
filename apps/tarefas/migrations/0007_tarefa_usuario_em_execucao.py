from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tarefas', '0006_alter_tarefa_status'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='tarefa',
            name='usuario',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name='tarefas_separacao', to=settings.AUTH_USER_MODEL, verbose_name='usuario responsavel'),
        ),
        migrations.AlterField(
            model_name='tarefa',
            name='status',
            field=models.CharField(choices=[('ABERTO', 'Aberto'), ('EM_EXECUCAO', 'Em execucao'), ('CONCLUIDO', 'Concluido'), ('FECHADO_COM_RESTRICAO', 'Fechado com restricao'), ('LIBERADO_COM_RESTRICAO', 'Liberado com restricao'), ('CONCLUIDO_COM_RESTRICAO', 'Concluido com restricao')], db_index=True, max_length=30, verbose_name='status'),
        ),
        migrations.AddIndex(
            model_name='tarefa',
            index=models.Index(fields=['usuario', 'status'], name='tarefa_usuario_status_idx'),
        ),
    ]