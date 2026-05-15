import uuid

from django.db import migrations
from django.db.models import Count


def separar_lotes_legados(apps, schema_editor):
	MinutaRomaneio = apps.get_model('core', 'MinutaRomaneio')
	lotes_duplicados = (
		MinutaRomaneio.objects.values('importacao_lote')
		.annotate(total=Count('id'))
		.filter(total__gt=1)
	)
	for lote in lotes_duplicados:
		romaneios = list(
			MinutaRomaneio.objects.filter(importacao_lote=lote['importacao_lote']).order_by('-created_at', '-id')
		)
		for romaneio in romaneios[1:]:
			romaneio.importacao_lote = uuid.uuid4()
			romaneio.save(update_fields=['importacao_lote'])


class Migration(migrations.Migration):

	dependencies = [
		('core', '0003_minutaromaneio_importacao_lote'),
	]

	operations = [
		migrations.RunPython(separar_lotes_legados, migrations.RunPython.noop),
	]