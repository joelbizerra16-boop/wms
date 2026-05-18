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

    def test_default_settings_module_usa_prod_quando_database_url_existe(self):
        self.assertEqual(default_settings_module({'DATABASE_URL': 'postgresql://user:pass@host:6543/db'}), 'config.settings.prod')

    def test_is_production_environment_usa_environment_explicito(self):
        self.assertTrue(is_production_environment({'ENVIRONMENT': 'production'}))

    def _reload_prod_settings(self):
        sys.modules.pop('config.settings.prod', None)
        return importlib.import_module('config.settings.prod')

    def test_prod_settings_permite_collectstatic_sem_database_url(self):
        env_backup = {
            chave: os.environ.get(chave)
            for chave in ('DATABASE_URL', 'SECRET_KEY', 'DJANGO_SETTINGS_MODULE')
        }
        argv_backup = sys.argv[:]
        try:
            os.environ.pop('DATABASE_URL', None)
            os.environ['SECRET_KEY'] = 'test-secret'
            os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.prod'
            sys.argv = ['manage.py', 'collectstatic']
            prod_settings = self._reload_prod_settings()
            self.assertEqual(
                prod_settings.DATABASES['default']['ENGINE'],
                'django.db.backends.postgresql',
            )
        finally:
            sys.argv = argv_backup
            for chave, valor in env_backup.items():
                if valor is None:
                    os.environ.pop(chave, None)
                else:
                    os.environ[chave] = valor

    def test_prod_settings_aceita_database_url_direta_supabase(self):
        env_backup = {
            chave: os.environ.get(chave)
            for chave in ('DATABASE_URL', 'SECRET_KEY', 'DJANGO_SETTINGS_MODULE')
        }
        argv_backup = sys.argv[:]
        try:
            os.environ['SECRET_KEY'] = 'test-secret'
            os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.prod'
            os.environ['DATABASE_URL'] = 'postgresql://user:pass@db.abcdef.supabase.co:5432/postgres'
            sys.argv = ['manage.py', 'runserver']
            prod_settings = self._reload_prod_settings()
            self.assertIn('postgresql', prod_settings.DATABASES['default']['ENGINE'])
        finally:
            sys.argv = argv_backup
            for chave, valor in env_backup.items():
                if valor is None:
                    os.environ.pop(chave, None)
                else:
                    os.environ[chave] = valor