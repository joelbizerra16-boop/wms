from importlib import import_module
from unittest.mock import MagicMock

from django.test import SimpleTestCase


repair_migration = import_module('apps.core.migrations.0007_reconcile_minuta_schema_postgresql')


class MinutaRepairMigrationTests(SimpleTestCase):
    def test_dependencia_aponta_para_0006(self):
        self.assertIn(('core', '0006_minutaromaneio_tipo_minuta_idx'), repair_migration.Migration.dependencies)

    def test_reconciliacao_executa_sql_idempotente_no_postgresql(self):
        schema_editor = MagicMock()
        schema_editor.connection.vendor = 'postgresql'
        cursor = MagicMock()
        cursor.fetchone.return_value = [True]
        schema_editor.connection.cursor.return_value.__enter__.return_value = cursor

        repair_migration.reconciliar_schema_minuta_postgresql(None, schema_editor)

        comandos = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any('ADD COLUMN IF NOT EXISTS hash_operacional' in comando for comando in comandos))
        self.assertTrue(any('ADD COLUMN IF NOT EXISTS status_expedicao' in comando for comando in comandos))
        self.assertTrue(any('ADD COLUMN IF NOT EXISTS tipo_minuta' in comando for comando in comandos))
        self.assertTrue(any('CREATE INDEX IF NOT EXISTS min_rom_exp_pdf_ix' in comando for comando in comandos))

    def test_reconciliacao_ignora_bancos_nao_postgresql(self):
        schema_editor = MagicMock()
        schema_editor.connection.vendor = 'sqlite'

        repair_migration.reconciliar_schema_minuta_postgresql(None, schema_editor)

        schema_editor.connection.cursor.assert_not_called()