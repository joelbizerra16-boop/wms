from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.conferencia.models import Conferencia
from apps.nf.models import NotaFiscal, NotaFiscalItem
from apps.produtos.models import Produto
from apps.rotas.models import Rota
from apps.tarefas.models import Tarefa, TarefaItem
from apps.usuarios.models import Setor, Usuario


@override_settings(ROOT_URLCONF='config.urls')
class ImportadorXMLAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.usuario = Usuario.objects.create_user(
            username='gestor',
            nome='Gestor',
            perfil=Usuario.Perfil.GESTOR,
            setores=[Setor.Codigo.NAO_ENCONTRADO],
            password='123456',
            is_staff=True,
        )
        self.client.force_authenticate(self.usuario)
        self.rota_cep = Rota.objects.create(nome='Rota CEP', cep_inicial='01000000', cep_final='19999999')

    def _upload(self, xml_content, filename='nfe.xml'):
        arquivo = SimpleUploadedFile(filename, xml_content.encode('utf-8'), content_type='text/xml')
        return self.client.post('/api/importar-xml/', {'file': arquivo}, format='multipart')

    def test_importa_nfe_e_cria_nf_itens_tarefas(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe35111111111111111111550010000000011000000010" versao="4.00">
      <ide>
        <nNF>123</nNF>
        <dhEmi>2026-04-23T10:00:00-03:00</dhEmi>
      </ide>
      <dest>
        <xNome>Cliente Teste</xNome>
        <IE>123456789</IE>
        <enderDest>
          <xBairro>Centro</xBairro>
          <CEP>01001000</CEP>
        </enderDest>
      </dest>
      <det nItem="1">
        <prod>
          <cProd>PRD001</cProd>
          <cEAN>789123</cEAN>
          <xProd>Produto A</xProd>
          <qCom>2.00</qCom>
        </prod>
      </det>
      <det nItem="2">
        <prod>
          <cProd>PRD002</cProd>
          <cEAN>789456</cEAN>
          <xProd>Filtro B</xProd>
          <qCom>1.00</qCom>
        </prod>
      </det>
    </infNFe>
  </NFe>
  <protNFe>
    <infProt>
      <cStat>100</cStat>
    </infProt>
  </protNFe>
</nfeProc>
"""
        response = self._upload(xml)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['sucesso'])
        self.assertEqual(response.data['quantidade_itens_importados'], 2)
        self.assertEqual(NotaFiscal.objects.count(), 1)
        self.assertEqual(NotaFiscalItem.objects.count(), 2)
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertEqual(Tarefa.objects.count(), 1)
        self.assertEqual(TarefaItem.objects.count(), 2)
        tarefa = Tarefa.objects.get()
        self.assertEqual(tarefa.tipo, Tarefa.Tipo.ROTA)
        self.assertEqual(tarefa.setor, Usuario.Setor.NAO_ENCONTRADO)
        self.assertEqual(TarefaItem.objects.filter(nf__numero='123').count(), 2)

    def test_reimportacao_cancelada_atualiza_nf_e_cancela_fluxos(self):
        xml_autorizada = """<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe35111111111111111111550010000000011000000010" versao="4.00">
      <ide>
        <nNF>123</nNF>
        <dhEmi>2026-04-23T10:00:00-03:00</dhEmi>
      </ide>
      <dest>
        <xNome>Cliente Teste</xNome>
        <IE>123456789</IE>
        <enderDest>
          <xBairro>Centro</xBairro>
          <CEP>01001000</CEP>
        </enderDest>
      </dest>
      <det nItem="1">
        <prod>
          <cProd>PRD001</cProd>
          <cEAN>789123</cEAN>
          <xProd>Produto A</xProd>
          <qCom>2.00</qCom>
        </prod>
      </det>
    </infNFe>
  </NFe>
  <protNFe>
    <infProt>
      <cStat>100</cStat>
    </infProt>
  </protNFe>
</nfeProc>
"""
        self._upload(xml_autorizada)
        nf = NotaFiscal.objects.get()
        Conferencia.objects.create(nf=nf, conferente=self.usuario, status=Conferencia.Status.AGUARDANDO)

        xml_cancelada = """<?xml version="1.0" encoding="UTF-8"?>
<procEventoNFe xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.00">
  <evento>
    <infEvento>
      <chNFe>35111111111111111111550010000000011000000010</chNFe>
      <tpEvento>110111</tpEvento>
    </infEvento>
  </evento>
  <retEvento>
    <infEvento>
      <cStat>135</cStat>
    </infEvento>
  </retEvento>
</procEventoNFe>
"""
        response = self._upload(xml_cancelada, filename='cancelamento.xml')
        nf.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'duplicada')
        self.assertEqual(nf.status_fiscal, NotaFiscal.StatusFiscal.AUTORIZADA)
        self.assertFalse(nf.bloqueada)
        self.assertTrue(nf.ativa)
        self.assertEqual(Tarefa.objects.filter(nf=nf, status=Tarefa.Status.FECHADO_COM_RESTRICAO).count(), 0)
        self.assertEqual(Conferencia.objects.filter(nf=nf, status=Conferencia.Status.CANCELADA).count(), 0)

    def test_importa_sem_rota_cadastrada_e_cria_rota_operacional(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe35111111111111111111550010000000011000000077" versao="4.00">
      <ide>
        <nNF>777</nNF>
        <dhEmi>2026-04-23T10:00:00-03:00</dhEmi>
      </ide>
      <dest>
        <xNome>Cliente Sem Rota</xNome>
        <IE>99887766</IE>
        <enderDest>
          <xBairro>Bairro Inexistente</xBairro>
          <CEP>99999999</CEP>
        </enderDest>
      </dest>
      <det nItem="1">
        <prod>
          <cProd>PRD777</cProd>
          <cEAN>789777</cEAN>
          <xProd>Produto sem rota</xProd>
          <qCom>1.00</qCom>
        </prod>
      </det>
    </infNFe>
  </NFe>
  <protNFe>
    <infProt>
      <cStat>100</cStat>
    </infProt>
  </protNFe>
</nfeProc>
"""
        response = self._upload(xml, filename='sem_rota.xml')

        self.assertEqual(response.status_code, 200)
        nf = NotaFiscal.objects.get(numero='777')
        self.assertEqual(nf.rota.nome, 'AJUSTAR')
        self.assertEqual(nf.rota.bairro, 'AJUSTAR')

    def test_importacao_com_produtos_categorizados_aparece_na_lista_de_separacao_do_gestor(self):
        Produto.objects.create(cod_prod='PRD001', descricao='Produto A', cod_ean='789123', categoria=Produto.Categoria.LUBRIFICANTE)
        Produto.objects.create(cod_prod='PRD002', descricao='Filtro B', cod_ean='789456', categoria=Produto.Categoria.FILTROS)

        xml = """<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe35111111111111111111550010000000011000000011" versao="4.00">
      <ide>
        <nNF>124</nNF>
        <dhEmi>2026-04-23T10:00:00-03:00</dhEmi>
      </ide>
      <dest>
        <xNome>Cliente Teste</xNome>
        <IE>123456780</IE>
        <enderDest>
          <xBairro>Centro</xBairro>
          <CEP>01001000</CEP>
        </enderDest>
      </dest>
      <det nItem="1">
        <prod>
          <cProd>PRD001</cProd>
          <cEAN>789123</cEAN>
          <xProd>Produto A</xProd>
          <qCom>2.00</qCom>
        </prod>
      </det>
      <det nItem="2">
        <prod>
          <cProd>PRD002</cProd>
          <cEAN>789456</cEAN>
          <xProd>Filtro B</xProd>
          <qCom>1.00</qCom>
        </prod>
      </det>
    </infNFe>
  </NFe>
  <protNFe>
    <infProt>
      <cStat>100</cStat>
    </infProt>
  </protNFe>
</nfeProc>
"""

        response_import = self._upload(xml, filename='categorizada.xml')
        response_tarefas = self.client.get('/api/separacao/tarefas/')

        self.assertEqual(response_import.status_code, 200)
        self.assertEqual(response_tarefas.status_code, 200)
        self.assertEqual(Tarefa.objects.count(), 2)
        self.assertEqual({item['setor'] for item in response_tarefas.data}, {Usuario.Setor.LUBRIFICANTE, Usuario.Setor.FILTROS})
        self.assertTrue(any(item['nf_numero'] == '124' for item in response_tarefas.data))

    def test_importacao_normaliza_produto_sem_categoria_e_gera_tarefa_nao_encontrado(self):
        produto = Produto.objects.create(cod_prod='PRD900', descricao='Produto sem categoria', cod_ean='789900', categoria='')

        xml = """<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe35111111111111111111550010000000011000000900" versao="4.00">
      <ide>
        <nNF>900</nNF>
        <dhEmi>2026-04-23T10:00:00-03:00</dhEmi>
      </ide>
      <dest>
        <xNome>Cliente Categoria</xNome>
        <IE>123123123</IE>
        <enderDest>
          <xBairro>Centro</xBairro>
          <CEP>01001000</CEP>
        </enderDest>
      </dest>
      <det nItem="1">
        <prod>
          <cProd>PRD900</cProd>
          <cEAN>789900</cEAN>
          <xProd>Produto sem categoria</xProd>
          <qCom>3.00</qCom>
        </prod>
      </det>
    </infNFe>
  </NFe>
  <protNFe>
    <infProt>
      <cStat>100</cStat>
    </infProt>
  </protNFe>
</nfeProc>
"""

        response = self._upload(xml, filename='sem_categoria.xml')
        produto.refresh_from_db()
        tarefa = Tarefa.objects.get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(produto.categoria, Produto.Categoria.NAO_ENCONTRADO)
        self.assertEqual(tarefa.setor, Usuario.Setor.NAO_ENCONTRADO)
        self.assertEqual(TarefaItem.objects.filter(tarefa=tarefa, produto=produto).count(), 1)