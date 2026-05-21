from django.test import SimpleTestCase

from apps.core.bipagem_leitura import (
    eh_bipagem_duplicada,
    sanitizar_entrada_scanner,
    variantes_codigo_barras,
)


class BipagemLeituraTests(SimpleTestCase):
    def test_sanitizar_remove_crlf_e_tabs(self):
        self.assertEqual(sanitizar_entrada_scanner(' 789\r\n\t'), '789')

    def test_variantes_ordem_enterprise(self):
        variantes = variantes_codigo_barras('00117896636550800')
        self.assertEqual(variantes[0], '00117896636550800')
        self.assertIn('17896636550800', variantes)
        self.assertIn(variantes[-1], variantes)
        if len('00117896636550800') >= 13:
            self.assertIn('00117896636550800'[-13:], variantes)

    def test_variantes_codigo_interno(self):
        self.assertEqual(variantes_codigo_barras('PRD001'), ['PRD001'])

    def test_anti_duplicata_janela_curta(self):
        self.assertFalse(
            eh_bipagem_duplicada(modulo='sep', entidade_id=1, usuario_id=9, codigo='789')
        )
        self.assertTrue(
            eh_bipagem_duplicada(modulo='sep', entidade_id=1, usuario_id=9, codigo='789')
        )
