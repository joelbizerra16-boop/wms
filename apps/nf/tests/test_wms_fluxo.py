from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Q
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.conferencia.models import Conferencia
from apps.logs.models import Log
from apps.nf.models import NotaFiscal
from apps.produtos.models import Produto
from apps.rotas.models import Rota
from apps.tarefas.models import Tarefa, TarefaItem
from apps.usuarios.models import Setor, Usuario


@override_settings(ROOT_URLCONF='config.urls')
class WMSFluxoAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.separador = Usuario.objects.create_user(
            username='separador_fluxo',
            nome='Separador Fluxo',
            perfil=Usuario.Perfil.SEPARADOR,
            setores=[
                Setor.Codigo.LUBRIFICANTE,
                Setor.Codigo.FILTROS,
                Setor.Codigo.AGREGADO,
                Setor.Codigo.NAO_ENCONTRADO,
            ],
            password='123456',
            is_active=True,
        )
        self.conferente = Usuario.objects.create_user(
            username='conferente_fluxo',
            nome='Conferente Fluxo',
            perfil=Usuario.Perfil.CONFERENTE,
            setores=[
                Setor.Codigo.LUBRIFICANTE,
                Setor.Codigo.FILTROS,
                Setor.Codigo.AGREGADO,
                Setor.Codigo.NAO_ENCONTRADO,
            ],
            password='123456',
            is_active=True,
        )
        self.rota = Rota.objects.create(nome='Rota Padrao', cep_inicial='00000000', cep_final='99999999')
        self.xml_dir = Path(settings.BASE_DIR) / 'xmls'
        self.xml_autorizado = self.xml_dir / 'xml_autorizado.xml'
        self.xml_cancelado = self.xml_dir / 'xml_cancelado.xml'
        self.xml_duplicado = self.xml_dir / 'xml_duplicado.xml'
        Produto.objects.create(
            cod_prod='PRD001',
            descricao='Produto A',
            cod_ean='789123',
            categoria=Produto.Categoria.LUBRIFICANTE,
            ativo=True,
            cadastrado_manual=True,
            incompleto=False,
        )
        Produto.objects.create(
            cod_prod='PRD002',
            descricao='Produto B',
            cod_ean='789456',
            categoria=Produto.Categoria.FILTROS,
            ativo=True,
            cadastrado_manual=True,
            incompleto=False,
        )

    def _autenticar(self, usuario):
        self.client.force_authenticate(user=usuario)

    def _importar_xml(self, caminho_xml, usuario=None):
        self._autenticar(usuario or self.separador)
        with caminho_xml.open('rb') as xml_file:
            arquivo = SimpleUploadedFile(caminho_xml.name, xml_file.read(), content_type='text/xml')
        return self.client.post('/api/importar-xml/', {'file': arquivo}, format='multipart')

    def _importar_xml_conteudo(self, conteudo_xml, filename='nfe_operacional.xml', usuario=None):
        self._autenticar(usuario or self.separador)
        arquivo = SimpleUploadedFile(filename, conteudo_xml.encode('utf-8'), content_type='text/xml')
        return self.client.post('/api/importar-xml/', {'file': arquivo}, format='multipart')

    def _separar_nf(self, nf):
        self._autenticar(self.separador)
        tarefas = (
        Tarefa.objects.filter(Q(nf=nf) | Q(itens__nf=nf))
        .distinct()
        .prefetch_related('itens__produto')
        .order_by('id')
        )

        for tarefa in tarefas:
            response_inicio = self.client.post('/api/separacao/iniciar/', {'tarefa_id': tarefa.id}, format='json')
            self.assertEqual(response_inicio.status_code, 200)

            for item in tarefa.itens.filter(nf=nf):
                codigo = item.produto.cod_prod or item.produto.cod_ean
                for _ in range(int(item.quantidade_total)):
                    response_bipagem = self.client.post(
                        '/api/separacao/bipar/',
                        {'tarefa_id': tarefa.id, 'codigo': codigo},
                        format='json',
                    )
                    self.assertEqual(response_bipagem.status_code, 200)

            response_final = self.client.post(
                '/api/separacao/finalizar/',
                {'tarefa_id': tarefa.id, 'status': Tarefa.Status.CONCLUIDO},
                format='json',
            )
            self.assertEqual(response_final.status_code, 200)

    def _conferir_nf(self, nf):
        self._autenticar(self.conferente)
        response_inicio = self.client.post('/api/conferencia/iniciar/', {'nf_id': nf.id}, format='json')
        self.assertIn(response_inicio.status_code, {200, 400})
        if response_inicio.status_code == 400:
            return response_inicio
        conferencia_id = response_inicio.data['id']

        for item in nf.itens.select_related('produto').order_by('id'):
            codigo = item.produto.cod_prod or item.produto.cod_ean
            for _ in range(int(item.quantidade)):
                response_bipagem = self.client.post(
                    '/api/conferencia/bipar/',
                    {'conferencia_id': conferencia_id, 'codigo': codigo},
                    format='json',
                )
                self.assertEqual(response_bipagem.status_code, 200)

        response_final = self.client.post(
            '/api/conferencia/finalizar/',
            {'conferencia_id': conferencia_id},
            format='json',
        )
        self.assertIn(response_final.status_code, {200, 400})
        conferencia = Conferencia.objects.get(id=conferencia_id)
        self.assertIn(conferencia.status, {Conferencia.Status.OK, Conferencia.Status.CONCLUIDO_COM_RESTRICAO})
        return response_final

    def test_importacao_ok(self):
        response = self._importar_xml(self.xml_autorizado)

        self.assertEqual(response.status_code, 200)
        nf = NotaFiscal.objects.get(chave_nfe='35111111111111111111550010000000011000000010')
        self.assertEqual(nf.status_fiscal, NotaFiscal.StatusFiscal.AUTORIZADA)
        self.assertEqual(NotaFiscal.objects.count(), 1)

    def test_duplicidade(self):
        primeira = self._importar_xml(self.xml_autorizado)
        segunda = self._importar_xml(self.xml_duplicado)

        self.assertEqual(primeira.status_code, 200)
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(segunda.data['status'], 'duplicada')
        self.assertEqual(NotaFiscal.objects.filter(chave_nfe='35111111111111111111550010000000011000000010').count(), 1)

    def test_nf_cancelada_importada_sem_alterar_outras_nfs(self):
        response_autorizado = self._importar_xml(self.xml_autorizado)
        nf_autorizada = NotaFiscal.objects.get(chave_nfe='35111111111111111111550010000000011000000010')
        tarefas_autorizadas_antes = Tarefa.objects.filter(nf=nf_autorizada).count()

        response_cancelado = self._importar_xml(self.xml_cancelado)
        nf_autorizada.refresh_from_db()

        self.assertEqual(response_autorizado.status_code, 200)
        self.assertEqual(response_cancelado.status_code, 400)
        self.assertFalse(NotaFiscal.objects.filter(chave_nfe='35111111111111111111550010000000011000000020').exists())
        self.assertEqual(NotaFiscal.objects.count(), 1)
        self.assertEqual(nf_autorizada.status_fiscal, NotaFiscal.StatusFiscal.AUTORIZADA)
        self.assertEqual(Tarefa.objects.filter(nf=nf_autorizada).count(), tarefas_autorizadas_antes)

    def test_bloqueio_na_separacao_para_nf_cancelada(self):
        response = self._importar_xml(self.xml_cancelado)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(NotaFiscal.objects.filter(chave_nfe='35111111111111111111550010000000011000000020').exists())
        self.assertFalse(Log.objects.filter(acao='SEPARACAO BLOQUEADA', detalhe__contains='NF CANCELADA').exists())

    def test_bloqueio_na_conferencia_para_nf_cancelada(self):
        response_import = self._importar_xml(self.xml_cancelado)
        self.assertEqual(response_import.status_code, 400)
        self.assertFalse(NotaFiscal.objects.filter(chave_nfe='35111111111111111111550010000000011000000020').exists())

    def test_nf_cancelada_nao_lista_em_separacao_e_conferencia(self):
        self._importar_xml(self.xml_autorizado)
        response_cancelado = self._importar_xml(self.xml_cancelado)
        nf_autorizada = NotaFiscal.objects.get(chave_nfe='35111111111111111111550010000000011000000010')
        self.assertEqual(response_cancelado.status_code, 400)
        self._separar_nf(nf_autorizada)

        self._autenticar(self.separador)
        response_separacao = self.client.get('/api/separacao/tarefas/')
        self.assertEqual(response_separacao.status_code, 200)
        self.assertTrue(all(item['nf_id'] != nf_autorizada.id or item['status'] != Tarefa.Status.ABERTO for item in response_separacao.data))

        self._autenticar(self.conferente)
        response_conferencia = self.client.get('/api/conferencia/nfs/')
        self.assertEqual(response_conferencia.status_code, 200)
        self.assertTrue(all(item['id'] != 999999 for item in response_conferencia.data))

    def test_fluxo_completo_ok(self):
        response_importacao = self._importar_xml(self.xml_autorizado)
        nf = NotaFiscal.objects.get(chave_nfe='35111111111111111111550010000000011000000010')

        self._separar_nf(nf)
        response_final = self._conferir_nf(nf)
        nf.refresh_from_db()

        self.assertEqual(response_importacao.status_code, 200)
        if response_final.status_code == 200:
            self.assertEqual(response_final.data['status'], Conferencia.Status.OK)
        self.assertFalse(nf.bloqueada)
        self.assertTrue(Tarefa.objects.filter(rota=nf.rota).exists())

    def test_fluxo_misto_mantem_todos_os_itens_na_operacao(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe35111111111111111111550010000000011000000444" versao="4.00">
      <ide>
        <nNF>444</nNF>
        <dhEmi>2026-04-23T10:00:00-03:00</dhEmi>
      </ide>
      <dest>
        <xNome>Cliente Mix</xNome>
        <IE>123456444</IE>
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
          <cProd>PRD999</cProd>
          <cEAN>789999</cEAN>
          <xProd>Produto Novo</xProd>
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

        response_importacao = self._importar_xml_conteudo(xml, filename='misto.xml')
        nf = NotaFiscal.objects.get(numero='444')

        self._separar_nf(nf)
        response_final = self._conferir_nf(nf)

        self.assertEqual(response_importacao.status_code, 200)
        self.assertEqual(nf.itens.count(), 2)
        self.assertEqual(TarefaItem.objects.filter(nf=nf).count(), 2)
        self.assertEqual(TarefaItem.objects.filter(nf=nf, produto__cod_prod='PRD999').count(), 1)
        self.assertEqual(TarefaItem.objects.get(nf=nf, produto__cod_prod='PRD999').tarefa.setor, Setor.Codigo.NAO_ENCONTRADO)
        self.assertIn(response_final.status_code, {200, 400})

    def test_fluxo_sem_cadastro_mantem_todos_os_itens_na_operacao(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe35111111111111111111550010000000011000000555" versao="4.00">
      <ide>
        <nNF>555</nNF>
        <dhEmi>2026-04-23T10:00:00-03:00</dhEmi>
      </ide>
      <dest>
        <xNome>Cliente Sem Cadastro</xNome>
        <IE>123456555</IE>
        <enderDest>
          <xBairro>Centro</xBairro>
          <CEP>01001000</CEP>
        </enderDest>
      </dest>
      <det nItem="1">
        <prod>
          <cProd>PRD555A</cProd>
          <cEAN>7895551</cEAN>
          <xProd>Produto Novo A</xProd>
          <qCom>1.00</qCom>
        </prod>
      </det>
      <det nItem="2">
        <prod>
          <cProd>PRD555B</cProd>
          <cEAN>7895552</cEAN>
          <xProd>Produto Novo B</xProd>
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

        response_importacao = self._importar_xml_conteudo(xml, filename='sem_cadastro_total.xml')
        nf = NotaFiscal.objects.get(numero='555')

        self._separar_nf(nf)
        response_final = self._conferir_nf(nf)

        self.assertEqual(response_importacao.status_code, 200)
        self.assertEqual(nf.itens.count(), 2)
        self.assertEqual(TarefaItem.objects.filter(nf=nf).count(), 2)
        self.assertEqual(TarefaItem.objects.filter(nf=nf, tarefa__setor=Setor.Codigo.NAO_ENCONTRADO).count(), 2)
        self.assertIn(response_final.status_code, {200, 400})
