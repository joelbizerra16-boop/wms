from unittest import mock

from django.test import SimpleTestCase

from apps.core.core_migration_sync import (
    CORE_MIGRATIONS_ORDEM,
    migration_materializada_no_banco,
    sincronizar_historico_migrations_core,
)


class CoreMigrationSyncTests(SimpleTestCase):
    def test_ordem_inclui_0001_e_0008(self):
        self.assertEqual(CORE_MIGRATIONS_ORDEM[0], '0001_minuta_models')
        self.assertEqual(CORE_MIGRATIONS_ORDEM[-1], '0008_minutaromaneio_lote_created_idx')

    def test_0001_exige_duas_tabelas(self):
        cursor = mock.Mock()
        cursor.fetchone.side_effect = [(True,), (False,)]
        self.assertFalse(migration_materializada_no_banco(cursor, '0001_minuta_models'))

        cursor.fetchone.side_effect = [(True,), (True,)]
        self.assertTrue(migration_materializada_no_banco(cursor, '0001_minuta_models'))

    def test_sincronizar_registra_fake_em_ordem(self):
        conn = mock.Mock()
        conn.vendor = 'postgresql'
        cursor_cm = mock.MagicMock()
        cursor_cm.__enter__.return_value = mock.MagicMock()
        conn.cursor.return_value = cursor_cm
        with mock.patch(
            'apps.core.core_migration_sync.migrations_core_aplicadas',
            return_value=set(),
        ):
            with mock.patch(
                'apps.core.core_migration_sync.migration_materializada_no_banco',
                side_effect=lambda _cursor, nome: nome == '0001_minuta_models',
            ):
                with mock.patch(
                    'apps.core.core_migration_sync.registrar_migration_fake',
                ) as registrar:
                    registradas = sincronizar_historico_migrations_core(conn)
        self.assertEqual(registradas, ['0001_minuta_models'])
        registrar.assert_called_once_with(conn, '0001_minuta_models')
