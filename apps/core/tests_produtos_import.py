from io import BytesIO

import pandas as pd
from django.test import TestCase

from apps.core.services.cadastro_import_service import importar_produtos_arquivo
from apps.produtos.models import Produto


class ImportacaoProdutosExcelTests(TestCase):
    def _build_excel_file(self, rows):
        dataframe = pd.DataFrame(rows)
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            dataframe.to_excel(writer, index=False)
        buffer.seek(0)
        buffer.name = 'produtos.xlsx'
        return buffer

    def test_importacao_produtos_preserva_ean_grande_como_texto(self):
        arquivo = self._build_excel_file(
            [
                {
                    'COD_PROD': '11803',
                    'Código': '11803',
                    'Descrição': 'HOT WHEELS CITY PISTA ATAQUE DO CROCODIL',
                    'EMBALAGEM': 'PC',
                    'Código de Barras (EAN)': '194735109630',
                    'SETOR': 'AGREGADO',
                }
            ]
        )

        resultado = importar_produtos_arquivo(arquivo)

        self.assertEqual(resultado['criados'], 1)
        produto = Produto.objects.get(cod_prod='11803')
        self.assertEqual(produto.codigo, '11803')
        self.assertEqual(produto.cod_ean, '194735109630')
        self.assertEqual(produto.setor, 'AGREGADO')

    def test_importacao_produtos_ignora_nan_sem_quebrar_upload(self):
        arquivo = self._build_excel_file(
            [
                {
                    'COD_PROD': '14625',
                    'Código': '14625',
                    'Descrição': 'CF850/2 MANN',
                    'EMBALAGEM': 'PC',
                    'Código de Barras (EAN)': '',
                    'SETOR': 'FILTRO',
                }
            ]
        )

        resultado = importar_produtos_arquivo(arquivo)

        self.assertEqual(resultado['criados'], 1)
        produto = Produto.objects.get(cod_prod='14625')
        self.assertIsNone(produto.cod_ean)