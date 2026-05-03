from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nf", "0004_expand_notafiscal_statuses"),
    ]

    operations = [
        migrations.AddField(
            model_name="notafiscal",
            name="balcao",
            field=models.BooleanField(db_index=True, default=False, verbose_name="pedido balcao"),
        ),
    ]
