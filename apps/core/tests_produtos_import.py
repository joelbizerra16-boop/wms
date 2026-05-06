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

    def test_importacao_produtos_atualiza_existente_sem_alterar_campos_operacionais(self):
        produto = Produto.objects.create(
            cod_prod='20001',
            codigo='20001',
            descricao='DESCRICAO ANTIGA',
            cod_ean='789000000001',
            embalagem='CX',
            unidade='CX',
            setor='FILTROS',
            categoria=Produto.Categoria.FILTROS,
            ativo=True,
            cadastrado_manual=False,
            incompleto=True,
        )

        arquivo = self._build_excel_file(
            [
                {
                    'COD_PROD': '20001',
                    'Código': '20001',
                    'Descrição': 'DESCRICAO NOVA',
                    'EMBALAGEM': 'UN',
                    'Código de Barras (EAN)': '789000000999',
                    'SETOR': 'AGREGADO',
                }
            ]
        )

        resultado = importar_produtos_arquivo(arquivo)

        self.assertEqual(resultado['atualizados'], 1)
        produto.refresh_from_db()
        self.assertEqual(produto.descricao, 'DESCRICAO NOVA')
        self.assertEqual(produto.cod_ean, '789000000999')
        self.assertEqual(produto.embalagem, 'CX')
        self.assertEqual(produto.unidade, 'CX')
        self.assertEqual(produto.setor, 'FILTROS')
        self.assertEqual(produto.categoria, Produto.Categoria.FILTROS)
        self.assertFalse(produto.cadastrado_manual)
        self.assertTrue(produto.incompleto)

    def test_importacao_produtos_remove_sufixo_decimal_do_ean_sem_adicionar_zero(self):
        arquivo = self._build_excel_file(
            [
                {
                    'COD_PROD': '30001',
                    'Código': '30001',
                    'Descrição': 'PRODUTO EAN DECIMAL',
                    'EMBALAGEM': 'PC',
                    'Código de Barras (EAN)': '789123456789.0',
                    'SETOR': 'AGREGADO',
                }
            ]
        )

        resultado = importar_produtos_arquivo(arquivo)

        self.assertEqual(resultado['criados'], 1)
        produto = Produto.objects.get(cod_prod='30001')
        self.assertEqual(produto.cod_ean, '789123456789')

    def test_importacao_produtos_grande_processa_em_lotes_sem_falhar(self):
        rows = []
        for index in range(250):
            rows.append(
                {
                    'COD_PROD': f'BATCH{index:04d}',
                    'Código': f'BATCH{index:04d}',
                    'Descrição': f'PRODUTO LOTE {index}',
                    'EMBALAGEM': 'PC',
                    'Código de Barras (EAN)': f'789{index:09d}',
                    'SETOR': 'AGREGADO',
                }
            )

        arquivo = self._build_excel_file(rows)

        resultado = importar_produtos_arquivo(arquivo)

        self.assertEqual(resultado['criados'], 250)
        self.assertEqual(resultado['atualizados'], 0)
        self.assertEqual(Produto.objects.filter(cod_prod__startswith='BATCH').count(), 250)