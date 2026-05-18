from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('nf', '0013_nf_status_created_idx'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='notafiscal',
            name='nf_status_created_idx',
        ),
    ]