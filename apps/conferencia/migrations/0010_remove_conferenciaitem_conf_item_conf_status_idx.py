from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('conferencia', '0009_conferenciaitem_status_idx'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='conferenciaitem',
            name='conf_item_conf_status_idx',
        ),
    ]