from decimal import Decimal
from io import BytesIO

import pandas as pd
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.estoque.models import EstoqueFisico, PosicaoEstoque, SapVsWmsUpload
from apps.estoque.services.sap_vs_wms import (
    StatusConciliacao,
    _carregar_dataframe_planilha_sap,
    _normalizar_header,
    calcular_metricas,
    importar_planilha_sap,
    montar_linhas_conciliacao,
)
from apps.produtos.models import Produto

User = get_user_model()


def _planilha_sap_bytes(linhas):
    """linhas: lista de (codigo, descricao, total)."""
    df = pd.DataFrame(
        [{'CodProduto': c, 'Descricao': d, 'Total': t} for c, d, t in linhas],
    )
    buf = BytesIO()
    df.to_excel(buf, index=False, engine='openpyxl')
    buf.seek(0)
    return buf


def _planilha_layout_depositos(codigo, descricao, total, deposito_110=0, deposito_99=0):
    """Layout real SAP: depósitos numéricos + coluna Total consolidada."""
    return {
        'CodProduto': codigo,
        'Descricao': descricao,
        0: 0,
        1: 0,
        2: 0,
        110: deposito_110,
        66: 0,
        80: 0,
        81: 0,
        82: 0,
        99: deposito_99,
        'Total': total,
    }


class SapVsWmsImportTestCase(TestCase):
    def setUp(self):
        self.gestor = User.objects.create_user(
            username='gestor_sap',
            password='x',
            nome='Gestor',
            perfil=User.Perfil.GESTOR,
            setor=User.Setor.FILTROS,
        )

    def test_normalizar_header_total_com_espacos(self):
        self.assertEqual(_normalizar_header(' Total '), 'TOTAL')
        self.assertEqual(_normalizar_header('total'), 'TOTAL')
        self.assertEqual(_normalizar_header('TOTAL'), 'TOTAL')

    def test_import_aba_correta_em_workbook_multipagina(self):
        df_export = pd.DataFrame({'ID': [1], 'Total da linha': [999]})
        df_sap = pd.DataFrame([_planilha_layout_depositos(20005, 'ARLA 32', 99, deposito_110=99)])
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine='openpyxl') as writer:
            df_export.to_excel(writer, sheet_name='ExportSAP', index=False)
            df_sap.to_excel(writer, sheet_name='Conciliacao', index=False)
        buf.seek(0)
        df = _carregar_dataframe_planilha_sap(buf)
        self.assertIn('TOTAL', df.columns)
        self.assertIn('CODPRODUTO', df.columns)
        buf.seek(0)
        importar_planilha_sap(buf, self.gestor)
        self.assertEqual(
            SapVsWmsUpload.objects.get(codigo_produto='20005').quantidade_sap,
            Decimal('99'),
        )

    def test_import_usa_coluna_total_nao_deposito_99(self):
        df = pd.DataFrame(
            [_planilha_layout_depositos(20005, 'ARLA 32 GRANEL', 99, deposito_110=99, deposito_99=0)],
        )
        buf = BytesIO()
        df.to_excel(buf, index=False, engine='openpyxl')
        buf.seek(0)
        importar_planilha_sap(buf, self.gestor)
        registro = SapVsWmsUpload.objects.get(codigo_produto='20005')
        self.assertEqual(registro.quantidade_sap, Decimal('99'))

    def test_import_substituir_upload_anterior(self):
        buf1 = _planilha_sap_bytes([(20005, 'ARLA', 500)])
        importar_planilha_sap(buf1, self.gestor)
        self.assertEqual(SapVsWmsUpload.objects.count(), 1)

        buf2 = _planilha_sap_bytes([(20005, 'ARLA', 100), (30001, 'OUTRO', 50)])
        importar_planilha_sap(buf2, self.gestor)
        self.assertEqual(SapVsWmsUpload.objects.count(), 2)
        self.assertEqual(
            SapVsWmsUpload.objects.get(codigo_produto='20005').quantidade_sap,
            Decimal('100'),
        )


class SapVsWmsConciliacaoTestCase(TestCase):
    def setUp(self):
        self.gestor = User.objects.create_user(
            username='gestor_sap2',
            password='x',
            nome='Gestor',
            perfil=User.Perfil.GESTOR,
            setor=User.Setor.FILTROS,
        )
        self.pos1 = PosicaoEstoque.objects.create(
            codigo_posicao='1-1-2-1',
            rua='1',
            posicao='1',
            andar='2',
            lado='1',
        )
        self.pos2 = PosicaoEstoque.objects.create(
            codigo_posicao='1-1-3-1',
            rua='1',
            posicao='1',
            andar='3',
            lado='1',
        )
        Produto.objects.create(
            cod_prod='20005',
            descricao='ARLA 32',
            setor='LUBRIFICANTES',
        )
        EstoqueFisico.objects.create(
            codigo_produto='20005',
            descricao='ARLA 32',
            quantidade=Decimal('200'),
            posicao=self.pos1,
            fifo_nf='05/26-1',
            data_entrada=timezone.now(),
            nf_entrada='1',
            usuario_armazenagem=self.gestor,
        )
        EstoqueFisico.objects.create(
            codigo_produto='20005',
            descricao='ARLA 32',
            quantidade=Decimal('340'),
            posicao=self.pos2,
            fifo_nf='05/26-2',
            data_entrada=timezone.now(),
            nf_entrada='2',
            usuario_armazenagem=self.gestor,
        )
        SapVsWmsUpload.objects.create(
            codigo_produto='20005',
            descricao='ARLA 32',
            quantidade_sap=Decimal('540'),
            setor='',
            usuario_upload=self.gestor,
        )
        SapVsWmsUpload.objects.create(
            codigo_produto='8',
            descricao='KIT MOD MOBIL TROCA DE OLEO',
            quantidade_sap=Decimal('0'),
            setor='',
            usuario_upload=self.gestor,
        )

    def test_soma_wms_por_produto(self):
        linhas = montar_linhas_conciliacao()
        linha = next(l for l in linhas if l.codigo_produto == '20005')
        self.assertEqual(linha.quantidade_wms, Decimal('540'))
        self.assertEqual(linha.quantidade_sap, Decimal('540'))
        self.assertEqual(linha.status, StatusConciliacao.OK)
        self.assertEqual(linha.setor, 'LUBRIFICANTES')

    def test_status_divergente(self):
        SapVsWmsUpload.objects.filter(codigo_produto='20005').update(quantidade_sap=Decimal('500'))
        linhas = montar_linhas_conciliacao()
        linha = next(l for l in linhas if l.codigo_produto == '20005')
        self.assertEqual(linha.status, StatusConciliacao.DIVERGENTE)
        metricas = calcular_metricas(linhas)
        self.assertEqual(metricas.total_divergentes, 1)

    def test_exibe_produto_zerado_sap_e_wms(self):
        linhas = montar_linhas_conciliacao()
        linha = next(l for l in linhas if l.codigo_produto == '8')
        self.assertEqual(linha.quantidade_wms, Decimal('0'))
        self.assertEqual(linha.quantidade_sap, Decimal('0'))
        self.assertEqual(linha.status, StatusConciliacao.OK)


class SapVsWmsViewTestCase(TestCase):
    def setUp(self):
        self.gestor = User.objects.create_user(
            username='gestor_sap_web',
            password='x',
            nome='Gestor',
            perfil=User.Perfil.GESTOR,
            setor=User.Setor.FILTROS,
        )
        self.client = Client()
        self.client.force_login(self.gestor)

    def test_tela_200(self):
        resp = self.client.get(reverse('web-estoque-sap-vs-wms'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Acuracidade SAP vs WMS')

    def test_upload_post(self):
        buf = _planilha_sap_bytes([(20005, 'ARLA', 10)])
        arquivo = SimpleUploadedFile(
            'sap.xlsx',
            buf.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        resp = self.client.post(
            reverse('web-estoque-sap-vs-wms'),
            {'acao': 'upload', 'arquivo': arquivo},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(SapVsWmsUpload.objects.count(), 1)
