from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conferencia', '0008_conferencia_status_updated_idx'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='conferenciaitem',
            index=models.Index(fields=['conferencia', 'status'], name='conf_item_conf_status_idx'),
        ),
    ]
