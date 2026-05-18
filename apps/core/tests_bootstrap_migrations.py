from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase


class BootstrapCoreMigrationsTests(SimpleTestCase):
    def test_ignora_fora_do_postgresql(self):
        with mock.patch('apps.core.management.commands.bootstrap_core_migrations.connection') as conn:
            conn.vendor = 'sqlite'
            call_command('bootstrap_core_migrations')

    def test_registra_fake_0001_quando_tabela_existe(self):
        with mock.patch('apps.core.management.commands.bootstrap_core_migrations.connection') as conn:
            conn.vendor = 'postgresql'
            with mock.patch(
                'apps.core.management.commands.bootstrap_core_migrations._migrations_core_aplicadas',
                return_value=set(),
            ):
                with mock.patch(
                    'apps.core.management.commands.bootstrap_core_migrations._avaliar_migrations_para_fake',
                    return_value={'0001_minuta_models'},
                ):
                    with mock.patch(
                        'apps.core.management.commands.bootstrap_core_migrations._registrar_fake',
                    ) as registrar:
                        call_command('bootstrap_core_migrations')
                        registrar.assert_called_once_with(conn, '0001_minuta_models')
