from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.core.db_fixes import (
    aplicar_reconcile_schema_minuta_postgresql,
    diagnosticar_schema_minuta,
    mensagem_schema_minuta_inconsistente,
)


class Command(BaseCommand):
    help = 'Aplica SQL idempotente do schema da minuta (colunas 0005/0007) em PostgreSQL legado.'

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            self.stdout.write(self.style.WARNING('reconcile_minuta_schema: ignorado (nao e PostgreSQL).'))
            return

        if not aplicar_reconcile_schema_minuta_postgresql(connection):
            self.stdout.write(self.style.WARNING('reconcile_minuta_schema: tabela core_minutaromaneio ausente.'))
            return

        diagnostico = diagnosticar_schema_minuta(connection)
        if not diagnostico['resultado_validacao']:
            raise CommandError(mensagem_schema_minuta_inconsistente(diagnostico))

        self.stdout.write(self.style.SUCCESS('reconcile_minuta_schema: schema da minuta OK.'))
