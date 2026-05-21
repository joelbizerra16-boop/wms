from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conferencia', '0010_remove_conferenciaitem_conf_item_conf_status_idx'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='conferenciaitem',
            index=models.Index(
                fields=['conferencia', 'status', 'qtd_conferida'],
                name='conf_item_bipagem_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='conferencia',
            index=models.Index(
                fields=['status', 'nf'],
                name='conf_status_nf_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='conferencia',
            index=models.Index(
                fields=['conferente', 'status', 'updated_at'],
                name='conf_conferente_fila_idx',
            ),
        ),
    ]
