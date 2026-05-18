from io import StringIO
import os
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import OperationalError, connection

from apps.core.db_fixes import diagnosticar_schema_minuta, mensagem_schema_minuta_inconsistente


CORE_MINUTA_MIGRATIONS = [
    '0001_minuta_models',
    '0002_minutaromaneioitem_bairro',
    '0003_minutaromaneio_importacao_lote',
    '0004_backfill_minuta_importacao_lote_legado',
    '0005_minuta_expedicao_persistencia',
    '0006_minutaromaneio_tipo_minuta_idx',
    '0007_reconcile_minuta_schema_postgresql',
]

COLUNAS_CRITICAS_ROMANEIO = [
    'hash_operacional',
    'status_expedicao',
    'tipo_minuta',
    'pdf_gerado_em',
    'pdf_gerado_por_id',
]


class Command(BaseCommand):
    help = 'Executa um healthcheck operacional unico da minuta para producao.'

    def handle(self, *args, **options):
        inicio = time.perf_counter()
        settings_dict = connection.settings_dict

        self.stdout.write(self.style.SUCCESS('== HEALTHCHECK MINUTA =='))
        self.stdout.write(f"settings_module={os.environ.get('DJANGO_SETTINGS_MODULE', '-')}")
        self.stdout.write(f"alias={connection.alias}")
        self.stdout.write(f"vendor={connection.vendor}")
        self.stdout.write(f"engine={settings_dict.get('ENGINE') or '-'}")
        self.stdout.write(f"host={settings_dict.get('HOST') or '-'}")
        self.stdout.write(f"port={settings_dict.get('PORT') or '-'}")
        self.stdout.write(f"database={settings_dict.get('NAME') or '-'}")

        try:
            connection.ensure_connection()
        except OperationalError as exc:
            total_ms = round((time.perf_counter() - inicio) * 1000, 2)
            self.stdout.write(self.style.ERROR('SCHEMA_INVALIDO'))
            self.stdout.write(f'connection_error={exc}')
            self.stdout.write(f'execution_ms={total_ms}')
            self.stdout.write(self.style.WARNING('HEALTHCHECK FINALIZADO'))
            return

        if connection.vendor != 'postgresql':
            total_ms = round((time.perf_counter() - inicio) * 1000, 2)
            self.stdout.write(self.style.ERROR('SCHEMA_INVALIDO'))
            self.stdout.write('motivo=healthcheck detalhado exige PostgreSQL')
            self.stdout.write(f'execution_ms={total_ms}')
            self.stdout.write(self.style.WARNING('HEALTHCHECK FINALIZADO'))
            return

        with connection.cursor() as cursor:
            cursor.execute('SELECT current_schema()')
            schema_atual = cursor.fetchone()[0]
            self.stdout.write(f'current_schema={schema_atual}')

            self.stdout.write('showmigrations_core=')
            buffer = StringIO()
            call_command('showmigrations', 'core', stdout=buffer)
            for linha in buffer.getvalue().splitlines():
                self.stdout.write(f'  {linha}')

            cursor.execute(
                """
                SELECT name
                FROM django_migrations
                WHERE app = 'core'
                ORDER BY name
                """
            )
            migrations_aplicadas = {linha[0] for linha in cursor.fetchall()}
            self.stdout.write('status_core_migrations=')
            for nome in CORE_MINUTA_MIGRATIONS:
                status = '[X]' if nome in migrations_aplicadas else '[ ]'
                self.stdout.write(f'  {status} {nome}')

            migration_0005_aplicada = '0005_minuta_expedicao_persistencia' in migrations_aplicadas
            migration_0007_aplicada = '0007_reconcile_minuta_schema_postgresql' in migrations_aplicadas
            self.stdout.write(f'migration_0005_status={"[X]" if migration_0005_aplicada else "[ ]"}')
            self.stdout.write(f'migration_0005_registro_django_migrations={migration_0005_aplicada}')
            self.stdout.write(f'migration_0007_status={"[X]" if migration_0007_aplicada else "[ ]"}')
            self.stdout.write(f'migration_0007_registro_django_migrations={migration_0007_aplicada}')

            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'core_minutaromaneio'
                ORDER BY column_name
                """
            )
            colunas_romaneio = [linha[0] for linha in cursor.fetchall()]
            self.stdout.write(f'colunas_core_minutaromaneio={colunas_romaneio}')

            colunas_presentes = set(colunas_romaneio)
            self.stdout.write('colunas_criticas_core_minutaromaneio=')
            for coluna in COLUNAS_CRITICAS_ROMANEIO:
                self.stdout.write(f'  {coluna}={coluna in colunas_presentes}')

            contagens = {}
            for tabela in ('core_minutaromaneio', 'core_minutaromaneioitem'):
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = current_schema()
                          AND table_name = %s
                    )
                    """,
                    [tabela],
                )
                tabela_existe = cursor.fetchone()[0]
                if not tabela_existe:
                    contagens[tabela] = None
                    continue
                cursor.execute(f'SELECT COUNT(*) FROM "{tabela}"')
                contagens[tabela] = cursor.fetchone()[0]

            self.stdout.write(f"core_minutaromaneio_count={contagens['core_minutaromaneio']}")
            self.stdout.write(f"core_minutaromaneioitem_count={contagens['core_minutaromaneioitem']}")

        diagnostico = diagnosticar_schema_minuta(connection)
        if diagnostico['resultado_validacao']:
            self.stdout.write(self.style.SUCCESS('SCHEMA_OK'))
        else:
            self.stdout.write(self.style.ERROR('SCHEMA_INVALIDO'))
            self.stdout.write(mensagem_schema_minuta_inconsistente(diagnostico))

        total_ms = round((time.perf_counter() - inicio) * 1000, 2)
        self.stdout.write(f'execution_ms={total_ms}')
        self.stdout.write(self.style.SUCCESS('HEALTHCHECK FINALIZADO'))