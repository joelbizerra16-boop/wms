import os

from django.test import SimpleTestCase

from manage import _default_settings_module


class ManagePySettingsSelectionTests(SimpleTestCase):
    def test_default_settings_module_usa_dev_fora_do_render(self):
        env_backup = {chave: os.environ.get(chave) for chave in ('RENDER', 'RENDER_EXTERNAL_URL', 'RENDER_SERVICE_ID', 'RENDER_INSTANCE_ID')}
        try:
            for chave in env_backup:
                os.environ.pop(chave, None)
            self.assertEqual(_default_settings_module(), 'config.settings.dev')
        finally:
            for chave, valor in env_backup.items():
                if valor is not None:
                    os.environ[chave] = valor

    def test_default_settings_module_usa_prod_no_render(self):
        env_backup = {chave: os.environ.get(chave) for chave in ('RENDER', 'RENDER_EXTERNAL_URL', 'RENDER_SERVICE_ID', 'RENDER_INSTANCE_ID')}
        try:
            os.environ['RENDER_SERVICE_ID'] = 'srv-123'
            self.assertEqual(_default_settings_module(), 'config.settings.prod')
        finally:
            for chave in env_backup:
                os.environ.pop(chave, None)
            for chave, valor in env_backup.items():
                if valor is not None:
                    os.environ[chave] = valor