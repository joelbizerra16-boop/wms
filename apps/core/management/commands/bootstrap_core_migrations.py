"""
Marca migrations do app core como aplicadas quando o schema legado ja existe no PostgreSQL.

Evita: ProgrammingError: relation "core_minutaromaneio" already exists
em deploys brownfield (Supabase com tabelas criadas antes do django_migrations).
"""

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder


CORE_APP = 'core'

# Ordem obrigatoria — somente migrations que podem conflitar com schema existente.
MIGRATIONS_BOOTSTRAP = (
    '0001_minuta_models',
    '0002_minutaromaneioitem_bairro',
    '0003_minutaromaneio_importacao_lote',
    '0004_backfill_minuta_importacao_lote_legado',
    '0005_minuta_expedicao_persistencia',
    '0006_minutaromaneio_tipo_minuta_idx',
)


def _tabela_existe(cursor, nome_tabela):
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = %s
        )
        """,
        [nome_tabela],
    )
    return bool(cursor.fetchone()[0])


def _coluna_existe(cursor, nome_tabela, nome_coluna):
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s
              AND column_name = %s
        )
        """,
        [nome_tabela, nome_coluna],
    )
    return bool(cursor.fetchone()[0])


def _migrations_core_aplicadas(connection):
    recorder = MigrationRecorder(connection)
    return {nome for app, nome in recorder.applied_migrations() if app == CORE_APP}


def _registrar_fake(connection, nome_migration):
    MigrationRecorder(connection).record_applied(CORE_APP, nome_migration)


def _avaliar_migrations_para_fake(cursor):
    """Retorna set de migrations seguras para fake dado o schema atual."""
    fakes = set()
    if not _tabela_existe(cursor, 'core_minutaromaneio'):
        return fakes

    fakes.add('0001_minuta_models')

    if _tabela_existe(cursor, 'core_minutaromaneioitem'):
        fakes.add('0002_minutaromaneioitem_bairro')

    if _coluna_existe(cursor, 'core_minutaromaneio', 'importacao_lote'):
        fakes.update(
            {
                '0003_minutaromaneio_importacao_lote',
                '0004_backfill_minuta_importacao_lote_legado',
            }
        )

    if _coluna_existe(cursor, 'core_minutaromaneio', 'status_expedicao') and _coluna_existe(
        cursor, 'core_minutaromaneio', 'pdf_gerado_em'
    ):
        fakes.update(
            {
                '0005_minuta_expedicao_persistencia',
                '0006_minutaromaneio_tipo_minuta_idx',
            }
        )

    return fakes


class Command(BaseCommand):
    help = 'Registra migrations core ja materializadas no banco legado (antes do migrate).'

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            self.stdout.write(self.style.WARNING('bootstrap_core_migrations: ignorado (nao e PostgreSQL).'))
            return

        aplicadas = _migrations_core_aplicadas(connection)
        with connection.cursor() as cursor:
            candidatas = _avaliar_migrations_para_fake(cursor)

        registradas = []
        for nome in MIGRATIONS_BOOTSTRAP:
            if nome not in candidatas:
                continue
            if nome in aplicadas:
                continue
            _registrar_fake(connection, nome)
            registradas.append(nome)

        if registradas:
            self.stdout.write(
                self.style.SUCCESS(
                    f'bootstrap_core_migrations: fake aplicado em {", ".join(registradas)}'
                )
            )
        else:
            self.stdout.write('bootstrap_core_migrations: nada a fazer.')
