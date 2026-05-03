from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("conferencia", "0005_alter_conferencia_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="conferenciaitem",
            name="bipado_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="itens_bipados_conferencia",
                to=settings.AUTH_USER_MODEL,
                verbose_name="bipado por",
            ),
        ),
        migrations.AddField(
            model_name="conferenciaitem",
            name="data_bipagem",
            field=models.DateTimeField(blank=True, null=True, verbose_name="data da bipagem"),
        ),
    ]
