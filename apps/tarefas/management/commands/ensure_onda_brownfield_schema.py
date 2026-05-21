from django.core.management.base import BaseCommand
from django.db import connection

from apps.tarefas.db_onda_brownfield import aplicar_schema_onda_brownfield
from apps.tarefas.services.onda_schema import invalidate_schema_onda_cache


class Command(BaseCommand):
    help = 'Aplica ADD COLUMN / CREATE TABLE IF NOT EXISTS da onda no PostgreSQL (schema legado brownfield).'

    def handle(self, *args, **options):
        aplicar_schema_onda_brownfield(connection)
        invalidate_schema_onda_cache()
        self.stdout.write(self.style.SUCCESS('Schema brownfield da onda aplicado.'))
