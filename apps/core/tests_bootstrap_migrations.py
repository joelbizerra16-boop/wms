from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase


class BootstrapCoreMigrationsTests(SimpleTestCase):
    def test_ignora_fora_do_postgresql(self):
        with mock.patch('apps.core.management.commands.bootstrap_core_migrations.connection') as conn:
            conn.vendor = 'sqlite'
            call_command('bootstrap_core_migrations')

    def test_chama_sincronizar_no_postgresql(self):
        with mock.patch('apps.core.management.commands.bootstrap_core_migrations.connection') as conn:
            conn.vendor = 'postgresql'
            with mock.patch(
                'apps.core.management.commands.bootstrap_core_migrations.diagnosticar_divergencia_migrations_core',
                return_value={
                    'vendor': 'postgresql',
                    'aplicadas': [],
                    'divergencias': [],
                    'pendentes_reais': ['0001_minuta_models'],
                    'tabela_romaneio_existe': True,
                },
            ):
                with mock.patch(
                    'apps.core.management.commands.bootstrap_core_migrations.sincronizar_historico_migrations_core',
                    return_value=['0001_minuta_models'],
                ) as sincronizar:
                    call_command('bootstrap_core_migrations')
                    sincronizar.assert_called_once_with(conn)
