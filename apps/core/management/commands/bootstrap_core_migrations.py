from django.core.management.base import BaseCommand

from apps.core.core_migration_sync import (
    diagnosticar_divergencia_migrations_core,
    sincronizar_historico_migrations_core,
)
from django.db import connection


class Command(BaseCommand):
    help = 'Sincroniza django_migrations do app core com tabelas/colunas ja existentes (brownfield).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Apenas diagnostica; nao grava em django_migrations.',
        )

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            self.stdout.write(self.style.WARNING('bootstrap_core_migrations: ignorado (nao e PostgreSQL).'))
            return

        diagnostico = diagnosticar_divergencia_migrations_core(connection)
        self.stdout.write(f"vendor={diagnostico['vendor']}")
        self.stdout.write(f"tabela_romaneio_existe={diagnostico['tabela_romaneio_existe']}")
        self.stdout.write(f"aplicadas={', '.join(diagnostico['aplicadas']) or '-'}")
        if diagnostico['pendentes_reais']:
            self.stdout.write(
                self.style.WARNING(
                    'pendentes_reais_no_banco=' + ', '.join(diagnostico['pendentes_reais'])
                )
            )
        for mensagem in diagnostico['divergencias']:
            self.stdout.write(self.style.ERROR(mensagem))

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('bootstrap_core_migrations: dry-run (nenhum registro gravado).'))
            return

        registradas = sincronizar_historico_migrations_core(connection)
        if registradas:
            self.stdout.write(
                self.style.SUCCESS(
                    'bootstrap_core_migrations: historico sincronizado em '
                    + ', '.join(registradas)
                )
            )
        else:
            self.stdout.write('bootstrap_core_migrations: historico ja consistente.')
