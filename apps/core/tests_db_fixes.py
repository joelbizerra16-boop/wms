from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from apps.core.db_fixes import diagnosticar_schema_minuta, invalidar_cache_schema_fix, mensagem_schema_minuta_inconsistente


class DiagnosticarSchemaMinutaTests(SimpleTestCase):
    def setUp(self):
        invalidar_cache_schema_fix()
        self.connection = MagicMock()
        self.connection.vendor = 'postgresql'
        self.connection.alias = 'default'
        self.cursor = MagicMock()
        self.connection.cursor.return_value.__enter__.return_value = self.cursor
        self.connection.introspection = MagicMock()

    def test_detecta_schema_consistente(self):
        self.connection.introspection.table_names.return_value = ['core_minutaromaneio', 'core_minutaromaneioitem']
        self.connection.introspection.get_table_description.side_effect = [
            [SimpleNamespace(name='id'), SimpleNamespace(name='created_at'), SimpleNamespace(name='updated_at'), SimpleNamespace(name='codigo_romaneio'), SimpleNamespace(name='importacao_lote'), SimpleNamespace(name='data_saida'), SimpleNamespace(name='placa'), SimpleNamespace(name='motorista'), SimpleNamespace(name='usuario_importacao_id'), SimpleNamespace(name='pdf_gerado_em'), SimpleNamespace(name='pdf_gerado_por_id'), SimpleNamespace(name='tipo_minuta'), SimpleNamespace(name='hash_operacional'), SimpleNamespace(name='status_expedicao')],
            [SimpleNamespace(name='id'), SimpleNamespace(name='created_at'), SimpleNamespace(name='updated_at'), SimpleNamespace(name='romaneio_id'), SimpleNamespace(name='nf_id'), SimpleNamespace(name='numero_nota'), SimpleNamespace(name='fantasia'), SimpleNamespace(name='razao_social'), SimpleNamespace(name='bairro'), SimpleNamespace(name='status'), SimpleNamespace(name='duplicado'), SimpleNamespace(name='duplicidade_romaneio_codigo'), SimpleNamespace(name='duplicidade_data_saida'), SimpleNamespace(name='duplicidade_motorista'), SimpleNamespace(name='duplicidade_usuario'), SimpleNamespace(name='peso_kg'), SimpleNamespace(name='valor_total')],
        ]

        diagnostico = diagnosticar_schema_minuta(self.connection)

        self.assertTrue(diagnostico['resultado_validacao'])
        self.assertEqual(diagnostico['tabelas_faltantes'], [])
        self.assertEqual(diagnostico['colunas_faltantes'], {})

    def test_detecta_colunas_faltantes(self):
        self.connection.introspection.table_names.return_value = ['core_minutaromaneio', 'core_minutaromaneioitem']
        self.connection.introspection.get_table_description.side_effect = [
            [SimpleNamespace(name='id'), SimpleNamespace(name='codigo_romaneio')],
            [SimpleNamespace(name='id'), SimpleNamespace(name='romaneio_id')],
        ]

        diagnostico = diagnosticar_schema_minuta(self.connection)

        self.assertFalse(diagnostico['resultado_validacao'])
        self.assertIn('core_minutaromaneio', diagnostico['colunas_faltantes'])
        self.assertIn('status_expedicao', diagnostico['colunas_faltantes']['core_minutaromaneio'])

    def test_mensagem_informa_migrate_quando_schema_inconsistente(self):
        diagnostico = {
            'erro': '',
            'tabelas_faltantes': [],
            'colunas_faltantes': {'core_minutaromaneio': ['status_expedicao']},
        }

        mensagem = mensagem_schema_minuta_inconsistente(diagnostico)

        self.assertIn('python manage.py migrate', mensagem)
        self.assertIn('0005_minuta_expedicao_persistencia', mensagem)
        self.assertIn('0007_reconcile_minuta_schema_postgresql', mensagem)
