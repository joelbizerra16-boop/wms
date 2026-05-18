from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import OperationalError
from django.test import SimpleTestCase


class HealthcheckMinutaCommandTests(SimpleTestCase):
    @patch('apps.core.management.commands.healthcheck_minuta.connection.ensure_connection')
    @patch('apps.core.management.commands.healthcheck_minuta.connection.settings_dict', {'ENGINE': 'django.db.backends.postgresql', 'HOST': 'localhost', 'PORT': 5432, 'NAME': 'wms'})
    @patch('apps.core.management.commands.healthcheck_minuta.connection.vendor', 'postgresql')
    @patch('apps.core.management.commands.healthcheck_minuta.connection.alias', 'default')
    def test_healthcheck_finaliza_quando_conexao_falha(self, ensure_connection_mock):
        ensure_connection_mock.side_effect = OperationalError('Connection refused')
        stdout = StringIO()

        call_command('healthcheck_minuta', stdout=stdout)

        saida = stdout.getvalue()
        self.assertIn('alias=default', saida)
        self.assertIn('vendor=postgresql', saida)
        self.assertIn('SCHEMA_INVALIDO', saida)
        self.assertIn('connection_error=Connection refused', saida)
        self.assertIn('HEALTHCHECK FINALIZADO', saida)
