from django.core.management.base import BaseCommand
from django.db import connection

from apps.estoque.db_schema import aplicar_schema_estoque_brownfield, tabelas_estoque_existem


class Command(BaseCommand):
    help = 'Garante tabelas do estoque endereçado no PostgreSQL (CREATE IF NOT EXISTS).'

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            self.stdout.write(self.style.WARNING('Ignorado: não é PostgreSQL.'))
            return
        if tabelas_estoque_existem(connection):
            self.stdout.write(self.style.SUCCESS('Schema estoque já presente.'))
            return
        if aplicar_schema_estoque_brownfield(connection):
            self.stdout.write(self.style.SUCCESS('Schema estoque aplicado (brownfield).'))
        else:
            self.stderr.write(self.style.ERROR('Falha ao aplicar schema estoque.'))
