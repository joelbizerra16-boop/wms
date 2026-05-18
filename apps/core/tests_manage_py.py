import importlib
import os
import sys

from django.test import SimpleTestCase

from manage import _default_settings_module
from config.settings_selector import default_settings_module, is_production_environment


class ManagePySettingsSelectionTests(SimpleTestCase):
    def test_default_settings_module_usa_dev_fora_do_render(self):
        env_backup = {chave: os.environ.get(chave) for chave in ('RENDER', 'RENDER_EXTERNAL_URL', 'RENDER_SERVICE_ID', 'RENDER_INSTANCE_ID', 'DATABASE_URL', 'ENVIRONMENT', 'APP_ENV')}
        try:
            for chave in env_backup:
                os.environ.pop(chave, None)
            self.assertEqual(_default_settings_module(), 'config.settings.dev')
        finally:
            for chave, valor in env_backup.items():
                if valor is not None:
                    os.environ[chave] = valor

    def test_default_settings_module_usa_prod_no_render(self):
        env_backup = {chave: os.environ.get(chave) for chave in ('RENDER', 'RENDER_EXTERNAL_URL', 'RENDER_SERVICE_ID', 'RENDER_INSTANCE_ID', 'DATABASE_URL', 'ENVIRONMENT', 'APP_ENV')}
        try:
            os.environ['RENDER_SERVICE_ID'] = 'srv-123'
            self.assertEqual(_default_settings_module(), 'config.settings.prod')
        finally:
            for chave in env_backup:
                os.environ.pop(chave, None)
            for chave, valor in env_backup.items():
                if valor is not None:
                    os.environ[chave] = valor

    def test_default_settings_module_usa_prod_com_environment_production(self):
        self.assertEqual(default_settings_module({'ENVIRONMENT': 'production'}), 'config.settings.prod')

    def test_default_settings_module_nao_usa_prod_apenas_por_database_url(self):
        self.assertEqual(
            default_settings_module({'DATABASE_URL': 'postgresql://user:pass@db.example.com:5432/postgres'}),
            'config.settings.dev',
        )

    def test_is_production_environment_usa_environment_explicito(self):
        self.assertTrue(is_production_environment({'ENVIRONMENT': 'production'}))

    def _reload_settings(self, module_name):
        sys.modules.pop(module_name, None)
        return importlib.import_module(module_name)

    def test_build_settings_usa_backend_dummy(self):
        build_settings = self._reload_settings('config.settings.build')
        self.assertEqual(build_settings.DATABASES['default']['ENGINE'], 'django.db.backends.dummy')

    def test_prod_settings_exige_database_url(self):
        env_backup = {
            chave: os.environ.get(chave)
            for chave in ('DATABASE_URL', 'SECRET_KEY', 'DJANGO_SETTINGS_MODULE')
        }
        try:
            os.environ.pop('DATABASE_URL', None)
            os.environ['SECRET_KEY'] = 'test-secret'
            with self.assertRaises(RuntimeError):
                self._reload_settings('config.settings.prod')
        finally:
            for chave, valor in env_backup.items():
                if valor is None:
                    os.environ.pop(chave, None)
                else:
                    os.environ[chave] = valor

    def test_prod_settings_aceita_qualquer_host_postgresql(self):
        env_backup = {
            chave: os.environ.get(chave)
            for chave in ('DATABASE_URL', 'SECRET_KEY', 'DJANGO_SETTINGS_MODULE')
        }
        try:
            os.environ['SECRET_KEY'] = 'test-secret'
            os.environ['DATABASE_URL'] = 'postgresql://user:pass@db.abcdef.supabase.co:5432/postgres'
            prod_settings = self._reload_settings('config.settings.prod')
            self.assertIn('postgresql', prod_settings.DATABASES['default']['ENGINE'])
        finally:
            for chave, valor in env_backup.items():
                if valor is None:
                    os.environ.pop(chave, None)
                else:
                    os.environ[chave] = valor
