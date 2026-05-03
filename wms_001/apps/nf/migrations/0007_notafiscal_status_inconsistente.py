from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nf", "0006_notafiscal_balcao"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notafiscal",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDENTE", "Pendente"),
                    ("EM_CONFERENCIA", "Em conferencia"),
                    ("CONCLUIDO", "Concluido"),
                    ("CONCLUIDO_COM_RESTRICAO", "Concluido com restricao"),
                    ("NORMAL", "Normal"),
                    ("BLOQUEADA_COM_RESTRICAO", "Bloqueada com restricao"),
                    ("LIBERADA_COM_RESTRICAO", "Liberada com restricao"),
                    ("INCONSISTENTE", "Inconsistente"),
                ],
                db_index=True,
                default="PENDENTE",
                max_length=30,
                verbose_name="status operacional",
            ),
        ),
    ]
