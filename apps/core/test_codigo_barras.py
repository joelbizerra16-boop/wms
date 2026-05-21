from django.test import SimpleTestCase

from apps.core.services.produto_validacao_service import (
    _codigo_corresponde_identificador,
    _identificadores_produto,
    _normalizar_codigo,
    normalizar_codigo_barras,
)
from apps.produtos.models import Produto


class CodigoBarrasNormalizacaoTests(SimpleTestCase):
    def test_prefixo_scanner_zebra_14_ultimos_digitos(self):
        self.assertEqual(normalizar_codigo_barras('0117896636550800'), '17896636550800')

    def test_prefixo_duplo_zero(self):
        self.assertEqual(normalizar_codigo_barras('0012345678901234'), '12345678901234')

    def test_ean13_nao_trunca(self):
        self.assertEqual(normalizar_codigo_barras('7891234567890'), '7891234567890')

    def test_remove_espacos_e_nao_numericos(self):
        self.assertEqual(normalizar_codigo_barras(' 01-17896636550800 '), '17896636550800')

    def test_codigo_interno_alfanumerico_preservado(self):
        self.assertEqual(_normalizar_codigo('PRD001'), 'PRD001')

    def test_leitura_numerica_usa_motor_barras(self):
        self.assertEqual(_normalizar_codigo('0117896636550800', modulo='separacao'), '17896636550800')

    def test_correspondencia_ean13_cadastrado_com_prefixo_scanner(self):
        produto = Produto(cod_prod='X', cod_ean='7896636550800', codigo='', ativo=True)
        identificadores = _identificadores_produto(produto)
        leitura = _normalizar_codigo('0117896636550800')
        self.assertTrue(_codigo_corresponde_identificador(leitura, identificadores))
