from uuid import uuid4

from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('core', '0002_minutaromaneioitem_bairro'),
	]

	operations = [
		migrations.AddField(
			model_name='minutaromaneio',
			name='importacao_lote',
			field=models.UUIDField(db_index=True, default=uuid4, editable=False, verbose_name='lote da importacao'),
		),
	]