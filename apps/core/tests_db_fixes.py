from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.core.db_fixes import garantir_coluna_bairro, garantir_estrutura_minuta, invalidar_cache_schema_fix
from apps.core.models import MinutaRomaneio, MinutaRomaneioItem


class GarantirColunaBairroTests(SimpleTestCase):
    def setUp(self):
        invalidar_cache_schema_fix()
        self.connection = MagicMock()
        self.connection.vendor = 'postgresql'
        self.connection.alias = 'default'
        self.cursor = MagicMock()
        self.connection.cursor.return_value.__enter__.return_value = self.cursor

    def test_nao_faz_nada_fora_do_postgresql(self):
        self.connection.vendor = 'sqlite'

        resultado = garantir_coluna_bairro(self.connection)

        self.assertFalse(resultado)
        self.connection.cursor.assert_not_called()

    def test_nao_altera_quando_tabela_nao_existe(self):
        self.cursor.fetchone.return_value = None

        resultado = garantir_coluna_bairro(self.connection)

        self.assertFalse(resultado)
        self.assertEqual(self.cursor.execute.call_count, 1)

    @patch('apps.core.db_fixes._invalidar_cache_colunas_nota_fiscal')
    def test_cria_coluna_e_indice_quando_bairro_nao_existe(self, invalidar_cache_mock):
        self.cursor.fetchone.side_effect = [object(), None]

        resultado = garantir_coluna_bairro(self.connection)

        self.assertTrue(resultado)
        comandos = [call.args[0] for call in self.cursor.execute.call_args_list]
        self.assertTrue(any('ALTER TABLE "nf_notafiscal" ADD COLUMN IF NOT EXISTS "bairro" VARCHAR(100)' in comando for comando in comandos))
        self.assertTrue(any('CREATE INDEX IF NOT EXISTS "nf_bairro_idx" ON "nf_notafiscal" ("bairro")' in comando for comando in comandos))
        invalidar_cache_mock.assert_called_once()

    def test_nao_repete_fix_quando_coluna_ja_existe(self):
        self.cursor.fetchone.side_effect = [object(), object()]

        primeiro_resultado = garantir_coluna_bairro(self.connection)
        segundo_resultado = garantir_coluna_bairro(self.connection)

        self.assertTrue(primeiro_resultado)
        self.assertFalse(segundo_resultado)
        self.assertEqual(self.connection.cursor.call_count, 1)


class GarantirEstruturaMinutaTests(SimpleTestCase):
    def setUp(self):
        invalidar_cache_schema_fix()
        self.connection = MagicMock()
        self.connection.vendor = 'postgresql'
        self.connection.alias = 'default'
        self.cursor = MagicMock()
        self.connection.cursor.return_value.__enter__.return_value = self.cursor
        self.schema_editor = MagicMock()
        self.connection.schema_editor.return_value.__enter__.return_value = self.schema_editor

    def test_nao_faz_nada_fora_do_postgresql(self):
        self.connection.vendor = 'sqlite'

        resultado = garantir_estrutura_minuta(self.connection)

        self.assertFalse(resultado)
        self.connection.cursor.assert_not_called()
        self.connection.schema_editor.assert_not_called()

    def test_cria_tabelas_minuta_quando_ausentes(self):
        self.cursor.fetchone.side_effect = [None, None, object(), object(), object(), object()]
        self.cursor.fetchall.return_value = []

        resultado = garantir_estrutura_minuta(self.connection)

        self.assertTrue(resultado)
        self.schema_editor.create_model.assert_any_call(MinutaRomaneio)
        self.schema_editor.create_model.assert_any_call(MinutaRomaneioItem)

    def test_adiciona_colunas_legadas_quando_tabelas_ja_existem(self):
        self.cursor.fetchone.side_effect = [object(), object(), object(), None, object(), None]
        self.cursor.fetchall.return_value = [(1,), (2,)]

        resultado = garantir_estrutura_minuta(self.connection)

        self.assertTrue(resultado)
        comandos = [call.args[0] for call in self.cursor.execute.call_args_list]
        self.assertTrue(any('ALTER TABLE "core_minutaromaneio" ADD COLUMN IF NOT EXISTS "importacao_lote" UUID' in comando for comando in comandos))
        self.assertTrue(any('ALTER TABLE "core_minutaromaneioitem" ADD COLUMN IF NOT EXISTS "bairro" VARCHAR(100)' in comando for comando in comandos))
        self.cursor.executemany.assert_called_once()