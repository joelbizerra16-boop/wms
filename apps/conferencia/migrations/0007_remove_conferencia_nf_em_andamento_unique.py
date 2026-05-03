from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('conferencia', '0006_conferenciaitem_rastreabilidade_bipagem'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='conferencia',
            name='conferencia_nf_em_andamento_unique',
        ),
    ]