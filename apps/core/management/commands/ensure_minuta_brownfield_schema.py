from django.core.management.base import BaseCommand
from django.db import connection

from apps.core.db_minuta_brownfield import aplicar_schema_minuta_brownfield


class Command(BaseCommand):
    help = 'Aplica ADD COLUMN IF NOT EXISTS da minuta no PostgreSQL (schema legado brownfield).'

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            self.stdout.write(self.style.WARNING('Ignorado: nao e PostgreSQL.'))
            return
        aplicar_schema_minuta_brownfield(connection)
        self.stdout.write(self.style.SUCCESS('Schema brownfield da minuta aplicado.'))
