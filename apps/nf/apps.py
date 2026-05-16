from django.apps import AppConfig
from django.db.backends.signals import connection_created


class NfConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.nf'
    verbose_name = 'Notas Fiscais'

    def ready(self):
        connection_created.connect(_executar_correcoes_criticas_na_conexao, dispatch_uid='apps.nf.db_fixes.connection_created')


def _executar_correcoes_criticas_na_conexao(sender, connection, **kwargs):
    from apps.core.db_fixes import aplicar_correcoes_criticas

    aplicar_correcoes_criticas(connection)
