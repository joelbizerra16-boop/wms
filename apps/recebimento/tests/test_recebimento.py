from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.nf.models import NotaFiscal
from apps.recebimento.models import EstoqueTemporario
from apps.recebimento.services.importador_recebimento import importar_xml_recebimento
from apps.recebimento.services.validacao_recebimento import MENSAGEM_NF_VENDA, validar_documento_recebimento
from apps.recebimento.services.xml_parser import DocumentoRecebimentoXML, ItemRecebimentoXML, RecebimentoXMLError

User = get_user_model()


def _xml_nfe(*, tp_nf='0', nat_op='COMPRA', emit_nome='FORNECEDOR LTDA', emit_cnpj='11111111000111', dest_cnpj='22222222000199', cstat='100'):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe xmlns="http://www.portalfiscal.inf.br/nfe">
    <infNFe Id="NFe35250611111111000111550010000014581000014587" versao="4.00">
      <ide>
        <nNF>145877</nNF>
        <tpNF>{tp_nf}</tpNF>
        <natOp>{nat_op}</natOp>
        <dhEmi>2025-06-01T10:00:00-03:00</dhEmi>
      </ide>
      <emit>
        <CNPJ>{emit_cnpj}</CNPJ>
        <xNome>{emit_nome}</xNome>
      </emit>
      <dest>
        <CNPJ>{dest_cnpj}</CNPJ>
        <xNome>BRIDA LUBRIFICANTES LTDA</xNome>
      </dest>
      <det nItem="1">
        <prod>
          <cProd>123573</cProd>
          <xProd>MOBIL X3</xProd>
          <qCom>20.0000</qCom>
        </prod>
      </det>
    </infNFe>
  </NFe>
  <protNFe versao="4.00">
    <infProt>
      <cStat>{cstat}</cStat>
    </infProt>
  </protNFe>
</nfeProc>"""


class RecebimentoValidacaoTestCase(TestCase):
    def test_bloqueia_tp_nf_saida(self):
        doc = DocumentoRecebimentoXML(
            chave_nfe='1' * 44,
            numero='145877',
            tp_nf='1',
            nat_op='VENDA',
            emit_nome='BRIDA LUBRIFICANTES LTDA',
            emit_cnpj='33333333000133',
            dest_nome='CLIENTE',
            dest_cnpj='44444444000144',
            status_fiscal_cstat='100',
            itens=[ItemRecebimentoXML('P1', 'Prod', Decimal('1'))],
        )
        with self.assertRaisesMessage(RecebimentoXMLError, MENSAGEM_NF_VENDA):
            validar_documento_recebimento(doc)

    def test_bloqueia_brida_vendendo_para_cliente(self):
        doc = DocumentoRecebimentoXML(
            chave_nfe='2' * 44,
            numero='999',
            tp_nf='0',
            nat_op='VENDA DE MERCADORIA',
            emit_nome='BRIDA LUBRIFICANTES LTDA',
            emit_cnpj='55555555000155',
            dest_nome='CLIENTE FINAL',
            dest_cnpj='66666666000166',
            status_fiscal_cstat='100',
            itens=[ItemRecebimentoXML('P1', 'Prod', Decimal('1'))],
        )
        with self.assertRaisesMessage(RecebimentoXMLError, MENSAGEM_NF_VENDA):
            validar_documento_recebimento(doc)

    def test_aceita_entrada_compra(self):
        doc = DocumentoRecebimentoXML(
            chave_nfe='3' * 44,
            numero='145878',
            tp_nf='0',
            nat_op='COMPRA PARA REVENDA',
            emit_nome='FORNECEDOR ABC',
            emit_cnpj='77777777000177',
            dest_nome='BRIDA LUBRIFICANTES LTDA',
            dest_cnpj='88888888000188',
            status_fiscal_cstat='100',
            itens=[ItemRecebimentoXML('124136', 'DELVAC', Decimal('10'))],
        )
        validar_documento_recebimento(doc)


class RecebimentoImportacaoTestCase(TestCase):
    def setUp(self):
        self.gestor = User.objects.create_user(
            username='gestor_receb',
            password='x',
            nome='Gestor',
            perfil=User.Perfil.GESTOR,
            setor=User.Setor.FILTROS,
        )

    def test_importacao_cria_estoque_temp_sem_nf_operacional(self):
        arquivo = BytesIO(_xml_nfe().encode('utf-8'))
        arquivo.name = 'entrada.xml'
        resultado = importar_xml_recebimento(arquivo, usuario=self.gestor, nome_arquivo='entrada.xml')
        self.assertTrue(resultado['sucesso'])
        self.assertEqual(EstoqueTemporario.objects.filter(status=EstoqueTemporario.Status.TEMP).count(), 1)
        item = EstoqueTemporario.objects.get()
        self.assertEqual(item.canal, 'TEMP')
        self.assertEqual(item.produto_codigo, '123573')
        self.assertEqual(NotaFiscal.objects.count(), 0)

    def test_importacao_bloqueia_venda(self):
        arquivo = BytesIO(
            _xml_nfe(
                tp_nf='1',
                nat_op='VENDA',
                emit_nome='BRIDA LUBRIFICANTES LTDA',
                emit_cnpj='99999999000199',
                dest_cnpj='10101010000101',
            ).encode('utf-8')
        )
        arquivo.name = 'venda.xml'
        with self.assertRaises(RecebimentoXMLError):
            importar_xml_recebimento(arquivo, usuario=self.gestor)
