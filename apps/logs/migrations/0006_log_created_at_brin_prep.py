from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Índice para consultas por período em logs/auditoria.
    Particionamento físico por data: aplicar via runbook PostgreSQL quando volume exigir.
    """

    dependencies = [
        ('logs', '0005_useractivitylog'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='log',
            index=models.Index(fields=['created_at'], name='log_created_at_idx'),
        ),
        migrations.AddIndex(
            model_name='useractivitylog',
            index=models.Index(fields=['timestamp'], name='user_act_timestamp_idx'),
        ),
    ]
