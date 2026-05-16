from django.core.management.base import BaseCommand
from django.db import connection

from apps.core.db_fixes import aplicar_correcoes_criticas


class Command(BaseCommand):
    help = 'Diagnostica a estrutura fisica da minuta no banco atual e pode aplicar o auto-fix.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--repair',
            action='store_true',
            help='Aplica as correcoes automaticas de schema antes de reexecutar o diagnostico.',
        )

    def handle(self, *args, **options):
        self._emitir_diagnostico('ANTES DO REPAIR')
        if options.get('repair'):
            corrigiu = aplicar_correcoes_criticas(connection)
            self.stdout.write(self.style.WARNING(f'Repair executado: {corrigiu}'))
            self._emitir_diagnostico('DEPOIS DO REPAIR')

    def _emitir_diagnostico(self, titulo):
        settings_dict = connection.settings_dict
        self.stdout.write(self.style.SUCCESS(f'== {titulo} =='))
        self.stdout.write(f"vendor={connection.vendor}")
        self.stdout.write(f"alias={connection.alias}")
        self.stdout.write(f"host={settings_dict.get('HOST') or '-'}")
        self.stdout.write(f"port={settings_dict.get('PORT') or '-'}")
        self.stdout.write(f"database={settings_dict.get('NAME') or '-'}")

        if connection.vendor != 'postgresql':
            self.stdout.write(self.style.WARNING('Diagnostico detalhado de schema disponivel apenas para PostgreSQL.'))
            return

        with connection.cursor() as cursor:
            cursor.execute('SELECT current_schema()')
            schema_atual = cursor.fetchone()[0]
            self.stdout.write(f"current_schema={schema_atual}")

            cursor.execute(
                """
                SELECT app, name
                FROM django_migrations
                WHERE app = 'core'
                  AND name IN (
                      '0001_minuta_models',
                      '0002_minutaromaneioitem_bairro',
                      '0003_minutaromaneio_importacao_lote',
                      '0004_backfill_minuta_importacao_lote_legado'
                  )
                ORDER BY name
                """
            )
            migrations_core = cursor.fetchall()
            self.stdout.write(f'migrations_core={migrations_core}')

            cursor.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_name LIKE '%minuta%'
                ORDER BY table_schema, table_name
                """
            )
            tabelas_minuta = cursor.fetchall()
            self.stdout.write(f'tabelas_minuta={tabelas_minuta}')

            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'core_minutaromaneio'
                ORDER BY ordinal_position
                """
            )
            colunas_romaneio = [linha[0] for linha in cursor.fetchall()]
            self.stdout.write(f'colunas_core_minutaromaneio={colunas_romaneio}')

            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'core_minutaromaneioitem'
                ORDER BY ordinal_position
                """
            )
            colunas_item = [linha[0] for linha in cursor.fetchall()]
            self.stdout.write(f'colunas_core_minutaromaneioitem={colunas_item}')
