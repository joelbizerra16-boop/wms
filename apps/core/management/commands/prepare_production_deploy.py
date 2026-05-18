"""
Pipeline unico de pre-deploy em producao (Render).

Ordem obrigatoria:
1. reconcile_minuta_schema  — colunas/indice idempotentes
2. bootstrap_core_migrations — django_migrations alinhado ao schema real
3. migrate --plan + migrate — somente o que falta
4. healthcheck_minuta — validacao final (falha o deploy se schema invalido)
"""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = 'Pre-deploy producao: reconcile, bootstrap migrations, migrate e healthcheck.'

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            raise CommandError('prepare_production_deploy exige PostgreSQL em producao.')

        self.stdout.write(self.style.SUCCESS('== PREPARE PRODUCTION DEPLOY =='))

        self.stdout.write('1/4 reconcile_minuta_schema')
        call_command('reconcile_minuta_schema', verbosity=1)

        self.stdout.write('2/4 bootstrap_core_migrations')
        call_command('bootstrap_core_migrations', verbosity=1)

        self.stdout.write('3/4 migrate --plan')
        plan_buffer = StringIO()
        call_command('migrate', '--plan', stdout=plan_buffer, verbosity=1)
        plan_output = plan_buffer.getvalue().strip()
        if plan_output:
            self.stdout.write(plan_output)
        else:
            self.stdout.write('  (nenhuma migration pendente)')

        self.stdout.write('3/4 migrate --noinput')
        call_command('migrate', '--noinput', verbosity=1)

        self.stdout.write('4/4 healthcheck_minuta')
        health_buffer = StringIO()
        try:
            call_command('healthcheck_minuta', stdout=health_buffer, verbosity=1)
        except SystemExit as exc:
            raise CommandError(f'healthcheck_minuta falhou (exit={exc.code})') from exc
        health_output = health_buffer.getvalue()
        self.stdout.write(health_output)
        if 'SCHEMA_INVALIDO' in health_output:
            raise CommandError('healthcheck_minuta reportou SCHEMA_INVALIDO.')

        self.stdout.write(self.style.SUCCESS('== PREPARE PRODUCTION DEPLOY OK =='))
