from django.test import SimpleTestCase


class HealthCheckTests(SimpleTestCase):
	def test_healthcheck_returns_success(self):
		response = self.client.get('/api/health/')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json(), {'status': 'ok'})


from datetime import timedelta
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

from django.core.exceptions import ObjectDoesNotExist
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import F
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from openpyxl import Workbook

from apps.clientes.models import Cliente
from apps.conferencia.models import Conferencia, ConferenciaItem
from apps.core.models import MinutaRomaneio, MinutaRomaneioItem
from apps.core.services.minuta_service import listar_minuta_itens
from apps.core.views_web import MAX_XML_FILES_POR_ENVIO
from apps.logs.models import LiberacaoDivergencia, Log
from apps.nf.models import EntradaNF, NotaFiscal, NotaFiscalItem
from apps.produtos.models import Produto
from apps.rotas.models import Rota
from apps.tarefas.models import Tarefa, TarefaItem
from apps.usuarios.models import Setor, Usuario
from apps.core.views_dashboard import _cliente_tarefa


def _build_minuta_workbook(rows, carga='5081690', motorista='8003 - CLAUDIO SOUZA DE JESUS', veiculo='52 - FTG6B24/BRIDA', data_saida='12/05/2026'):
	workbook = Workbook()
	worksheet = workbook.active
	worksheet.title = 'Romaneio'
	worksheet.append(['Relatório de Montagem de Carga'])
	worksheet.append([])
	worksheet.append(['Filial', 'Dt. Saída', 'Carga', 'Destino', 'KM', 'Rotas', 'Qtd. Pedidos', 'Qtd. Clientes', 'Veículo', 'Motorista', 'Ajudante 1', 'Ajudante 2', 'Ajudante 3', 'N°Box', 'Transportadora'])
	worksheet.append(['1 - BRIDA LUBRIFICANTES LTDA', data_saida, carga, 'BRIDA DISTR.', '241.423', '20,21', str(len(rows)), str(len(rows)), veiculo, motorista, ' - ', ' - ', ' - ', '-', '-'])
	worksheet.append([])
	worksheet.append(['Seq. Ent', 'Código', 'Fantasia', 'Fantasia', 'Razao Social', 'Razao Social', 'Carregamento', 'Número Nota', 'Número Pedido', 'Tipo Cobrança', 'Peso/Kg', 'Volume/M³', 'Valor Total'])
	for row in rows:
		worksheet.append(row)
	buffer = io.BytesIO()
	workbook.save(buffer)
	return buffer.getvalue()


def _build_nfe_xml(numero_nf, chave_nf, itens, rota='CAIEIRAS', inf_cpl=None):
	partes_itens = []
	for indice, item in enumerate(itens, start=1):
		partes_itens.append(
			f'''
			<det nItem="{indice}">
			  <prod>
			    <cProd>{item["codigo"]}</cProd>
			    <cEAN></cEAN>
			    <xProd>{item["descricao"]}</xProd>
			    <uCom>{item["unidade"]}</uCom>
			    <qCom>{item["quantidade"]}</qCom>
			    <vUnCom>1.00</vUnCom>
			    <vProd>1.00</vProd>
			  </prod>
			</det>
			'''.strip()
		)

	texto_inf_cpl = inf_cpl if inf_cpl is not None else f'Pedido teste - Rota: {rota}'

	return f'''<?xml version="1.0" encoding="utf-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
	<NFe>
		<infNFe versao="4.00" Id="NFe{chave_nf}">
			<ide>
				<cUF>35</cUF>
				<cNF>12345678</cNF>
				<natOp>Venda</natOp>
				<mod>55</mod>
				<serie>1</serie>
				<nNF>{numero_nf}</nNF>
				<dhEmi>2026-05-12T10:00:00-03:00</dhEmi>
				<tpNF>1</tpNF>
				<idDest>1</idDest>
				<cMunFG>3550308</cMunFG>
				<tpImp>1</tpImp>
				<tpEmis>1</tpEmis>
				<cDV>1</cDV>
				<tpAmb>1</tpAmb>
				<finNFe>1</finNFe>
				<indFinal>0</indFinal>
				<indPres>0</indPres>
				<procEmi>0</procEmi>
				<verProc>WMS</verProc>
			</ide>
			<dest>
				<xNome>CLIENTE XML</xNome>
				<IE>ISENTO</IE>
				<enderDest>
					<xBairro>CENTRO</xBairro>
					<CEP>01000000</CEP>
				</enderDest>
			</dest>
			{''.join(partes_itens)}
			<transp>
				<vol>
					<pesoB>57.600</pesoB>
				</vol>
			</transp>
			<infAdic>
				<infCpl>{texto_inf_cpl}</infCpl>
			</infAdic>
		</infNFe>
	</NFe>
	<protNFe versao="4.00">
		<infProt>
			<tpAmb>1</tpAmb>
			<verAplic>SP_NFE_PL009_V4</verAplic>
			<chNFe>{chave_nf}</chNFe>
			<dhRecbto>2026-05-12T10:00:01-03:00</dhRecbto>
			<nProt>135261613421924</nProt>
			<digVal>abc</digVal>
			<cStat>100</cStat>
			<xMotivo>Autorizado o uso da NF-e</xMotivo>
		</infProt>
	</protNFe>
</nfeProc>'''.encode('utf-8')


@override_settings(ROOT_URLCONF='config.urls')
class DashboardWebTests(TestCase):
	def setUp(self):
		self.client = Client()
		self.usuario = Usuario.objects.create_user(
			username='gestor_dashboard',
			nome='Gestor Dashboard',
			perfil=Usuario.Perfil.GESTOR,
			setores=[Setor.Codigo.FILTROS, Setor.Codigo.NAO_ENCONTRADO],
			password='123456',
			is_active=True,
		)
		self.client.login(username='gestor_dashboard', password='123456')

		self.usuario_conferente = Usuario.objects.create_user(
			username='conferente_dashboard',
			nome='Conferente Dashboard',
			perfil=Usuario.Perfil.CONFERENTE,
			setores=[Setor.Codigo.FILTROS],
			password='123456',
			is_active=True,
		)
		self.usuario_separador = Usuario.objects.create_user(
			username='separador_dashboard',
			nome='Separador Dashboard',
			perfil=Usuario.Perfil.SEPARADOR,
			setores=[Setor.Codigo.FILTROS],
			password='123456',
			is_active=True,
		)

		self.rota = Rota.objects.create(nome='L01', cep_inicial='01000000', cep_final='01999999')
		self.cliente = Cliente.objects.create(nome='Rodrigo', inscricao_estadual='111222333')
		self.produto_ok = Produto.objects.create(cod_prod='123223', descricao='10W40', cod_ean='789123223', categoria=Produto.Categoria.FILTROS)
		self.produto_pendente = Produto.objects.create(cod_prod='123039', descricao='15W40', cod_ean='789123039', categoria=Produto.Categoria.FILTROS)

		self.nf = NotaFiscal.objects.create(
			chave_nfe='35111111111111111111550010000000011000000555',
			numero='1410289',
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.PENDENTE,
			data_emissao='2026-04-24T10:00:00-03:00',
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=False,
			ativa=True,
		)
		NotaFiscalItem.objects.create(nf=self.nf, produto=self.produto_ok, quantidade='10.00')
		NotaFiscalItem.objects.create(nf=self.nf, produto=self.produto_pendente, quantidade='5.00')

		self.tarefa = Tarefa.objects.create(
			nf=None,
			tipo=Tarefa.Tipo.ROTA,
			setor=Setor.Codigo.FILTROS,
			rota=self.rota,
			status=Tarefa.Status.ABERTO,
		)
		TarefaItem.objects.create(tarefa=self.tarefa, nf=self.nf, produto=self.produto_ok, quantidade_total='10.00', quantidade_separada='10.00')
		TarefaItem.objects.create(tarefa=self.tarefa, nf=self.nf, produto=self.produto_pendente, quantidade_total='5.00', quantidade_separada='3.00')

		self.conferencia = Conferencia.objects.create(nf=self.nf, conferente=self.usuario, status=Conferencia.Status.EM_CONFERENCIA)
		ConferenciaItem.objects.create(conferencia=self.conferencia, produto=self.produto_ok, qtd_esperada='10.00', qtd_conferida='10.00', status=ConferenciaItem.Status.OK)
		ConferenciaItem.objects.create(conferencia=self.conferencia, produto=self.produto_pendente, qtd_esperada='5.00', qtd_conferida='3.00', status=ConferenciaItem.Status.AGUARDANDO)

	def test_dashboard_separacao_exibe_indicadores_e_linhas(self):
		response = self.client.get('/dashboard/separacao/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Dashboard de Separação')
		self.assertContains(response, '1410289')
		self.assertContains(response, 'Rodrigo')
		self.assertContains(response, '123223')
		self.assertContains(response, 'EM EXECUCAO')

	def test_dashboard_separacao_mantem_itens_concluidos_no_periodo(self):
		TarefaItem.objects.filter(tarefa=self.tarefa).update(quantidade_separada=F('quantidade_total'))
		self.tarefa.status = Tarefa.Status.CONCLUIDO
		self.tarefa.save(update_fields=['status', 'updated_at'])

		response = self.client.get('/dashboard/separacao/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '1410289')
		self.assertContains(response, 'SEPARADO')
		self.assertGreater(response.context['indicadores']['separado'], 0)

	def test_dashboard_conferencia_exibe_nf_e_resumo(self):
		response = self.client.get('/dashboard/conferencia/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Dashboard de Conferência')

	def test_dashboard_conferencia_nao_quebra_quando_nf_esta_sem_cliente_valido(self):
		class NFInconsistente:
			id = 987
			cliente_id = 123

			@property
			def cliente(self):
				raise ObjectDoesNotExist('cliente ausente')

		class ItemInconsistente:
			id = 321
			tarefa_id = 654
			nf_id = 987
			nf = NFInconsistente()
			tarefa = SimpleNamespace(nf_id=None, nf=None)

		with self.assertLogs('apps.core.views_dashboard', level='INFO') as logs:
			cliente = _cliente_tarefa(ItemInconsistente())

		self.assertEqual(cliente, 'CLIENTE NAO INFORMADO')
		self.assertTrue(any('Item sem cliente vinculado' in message for message in logs.output))

	def test_home_operacional_e_estatica_e_sem_polling(self):
		response = self.client.get('/home/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Home Operacional WMS')
		self.assertContains(response, 'Bem-vindo ao sistema WMS')
		self.assertNotContains(response, '/api/dashboard/', html=False)
		self.assertNotContains(response, 'dashboardRefreshIntervalMs', html=False)
		self.assertNotContains(response, 'fetch(', html=False)

	def test_home_dashboard_api_antiga_nao_esta_disponivel(self):
		response = self.client.get('/api/dashboard/')

		self.assertEqual(response.status_code, 404)

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_minuta_abre_com_estrutura_visual_estatica(self):
		response = self.client.get('/minuta/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'MINUTA')
		self.assertContains(response, 'Controle de Minutas Operacionais')
		self.assertContains(response, 'UPLOAD EXCEL')
		self.assertContains(response, 'GERAR PDF')
		self.assertContains(response, 'Minuta de Carregamento')
		self.assertContains(response, 'Minuta de Entrega')
		self.assertContains(response, 'target="_blank"', html=False)
		self.assertContains(response, 'download', html=False)
		self.assertContains(response, 'data-base-href', html=False)
		self.assertNotContains(response, 'id="minuta-tipo-entrega" type="checkbox" disabled', html=False)
		self.assertContains(response, 'minuta-upload-inline__controls', html=False)
		self.assertContains(response, 'minuta-check-row--inline', html=False)
		self.assertContains(response, 'ROMANEIO')
		self.assertContains(response, 'NF')
		self.assertContains(response, 'BAIRRO')
		self.assertContains(response, 'STATUS')
		self.assertNotContains(response, 'fetch(', html=False)
		self.assertNotContains(response, 'setInterval', html=False)


@override_settings(ROOT_URLCONF='config.urls')
class MinutaImportacaoTests(TestCase):
	def setUp(self):
		self.client = Client()
		self.usuario = Usuario.objects.create_user(
			username='gestor_minuta',
			nome='Gestor Minuta',
			perfil=Usuario.Perfil.GESTOR,
			setores=[Setor.Codigo.FILTROS],
			password='123456',
			is_active=True,
		)
		self.client.login(username='gestor_minuta', password='123456')
		self.rota = Rota.objects.create(nome='CAIEIRAS', cep_inicial='01000000', cep_final='01999999')
		self.cliente = Cliente.objects.create(nome='Cliente Minuta', inscricao_estadual='123123123')
		self.produto = Produto.objects.create(
			cod_prod='MIN001',
			descricao='MOBIL SUPER 3000 5W30 24X1L',
			cod_ean='789MIN001',
			unidade='CX',
			categoria=Produto.Categoria.FILTROS,
		)
		self.nf = NotaFiscal.objects.create(
			chave_nfe='35111111111111111111550010000000011000000777',
			numero='1414802',
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.PENDENTE,
			data_emissao='2026-05-12T10:00:00-03:00',
			bairro='Centro',
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=False,
			ativa=True,
		)
		NotaFiscalItem.objects.create(nf=self.nf, produto=self.produto, quantidade='2.00')

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_upload_minuta_sinaliza_duplicidade_por_numero_nota(self):
		romaneio_existente = MinutaRomaneio.objects.create(
			codigo_romaneio='5081000',
			filial='BRIDA',
			data_saida=timezone.datetime(2026, 5, 10).date(),
			placa='ABC1D23',
			motorista='Motorista Antigo',
			usuario_importacao=self.usuario,
		)
		MinutaRomaneioItem.objects.create(
			romaneio=romaneio_existente,
			nf=self.nf,
			numero_nota=self.nf.numero,
			status='NF VINCULADA',
		)
		arquivo = SimpleUploadedFile(
			'romaneio.xlsx',
			_build_minuta_workbook([
				('1', '29664', 'TRANSPORTES LUCAS', 'TRANSPORTES LUCAS', 'Cliente Minuta', 'Cliente Minuta', '5081690', self.nf.numero, '9999924589', 'MXS_15', '57.6', '0', '2186.36'),
			]),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)

		response = self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo}, follow=True)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Importação da minuta concluída')
		self.assertContains(response, 'DUPLI')
		self.assertContains(response, '5081000')
		self.assertContains(response, self.nf.numero)

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_confirmacao_importa_minuta_e_relaciona_nf_por_numero(self):
		romaneio_existente = MinutaRomaneio.objects.create(
			codigo_romaneio='5081000',
			filial='BRIDA',
			data_saida=timezone.datetime(2026, 5, 10).date(),
			placa='ABC1D23',
			motorista='Motorista Antigo',
			usuario_importacao=self.usuario,
		)
		MinutaRomaneioItem.objects.create(
			romaneio=romaneio_existente,
			nf=self.nf,
			numero_nota=self.nf.numero,
			status='NF VINCULADA',
		)
		nf_nova = NotaFiscal.objects.create(
			chave_nfe='35111111111111111111550010000000011000000999',
			numero='1419999',
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.PENDENTE,
			data_emissao='2026-05-12T11:00:00-03:00',
			bairro='Jardim Europa',
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=False,
			ativa=True,
		)
		NotaFiscalItem.objects.create(nf=nf_nova, produto=self.produto, quantidade='1.00')
		arquivo = SimpleUploadedFile(
			'romaneio.xlsx',
			_build_minuta_workbook([
				('1', '29664', 'TRANSPORTES LUCAS', 'TRANSPORTES LUCAS', 'Cliente Minuta', 'Cliente Minuta', '5081690', self.nf.numero, '9999924589', 'MXS_15', '57.6', '0', '2186.36'),
				('2', '55764', 'AUTO POSTO', 'AUTO POSTO', 'Cliente Novo', 'Cliente Novo', '5081690', '1419999', '9999924701', 'MXS_15', '21.94', '0', '1644.68'),
			]),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)

		response_upload = self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo}, follow=True)
		self.assertEqual(response_upload.status_code, 200)
		self.assertContains(response_upload, 'Importação da minuta concluída')
		self.assertContains(response_upload, 'Centro')
		self.assertContains(response_upload, 'Jardim Europa')
		romaneio = MinutaRomaneio.objects.get(codigo_romaneio='5081690')
		item_nf_existente = MinutaRomaneioItem.objects.get(romaneio=romaneio, numero_nota=self.nf.numero)
		item_nf_nova = MinutaRomaneioItem.objects.get(romaneio=romaneio, numero_nota='1419999')
		self.assertEqual(item_nf_existente.nf_id, self.nf.id)
		self.assertTrue(item_nf_existente.duplicado)
		self.assertEqual(item_nf_existente.duplicidade_romaneio_codigo, '5081000')
		self.assertFalse(item_nf_nova.duplicado)
		self.assertEqual(item_nf_nova.nf_id, nf_nova.id)
		self.assertEqual(item_nf_nova.status, 'XML IMPORTADO')

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_upload_minuta_bloqueia_nf_nao_localizada(self):
		arquivo = SimpleUploadedFile(
			'romaneio.xlsx',
			_build_minuta_workbook([
				('1', '55764', 'AUTO POSTO', 'AUTO POSTO', 'Cliente Novo', 'Cliente Novo', '5081690', '1419999', '9999924701', 'MXS_15', '21.94', '0', '1644.68'),
			]),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)

		response = self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo}, follow=True)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'A importação atual substituiu a versão anterior')
		self.assertContains(response, 'NF NÃO LOCALIZADA')
		self.assertTrue(MinutaRomaneio.objects.filter(codigo_romaneio='5081690').exists())
		self.assertTrue(MinutaRomaneioItem.objects.filter(romaneio__codigo_romaneio='5081690', numero_nota='1419999').exists())

		response_confirm = self.client.post('/minuta/', {'acao': 'confirmar_importacao'}, follow=True)
		self.assertEqual(response_confirm.status_code, 200)
		self.assertContains(response_confirm, 'Nenhuma prévia de importação está disponível para confirmação')

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_upload_minuta_importa_nf_aguardando_liberacao(self):
		EntradaNF.objects.create(
			chave_nf='35260400846804000106550010000001231027966310',
			numero_nf='123',
			xml='xmls/xml_autorizado.xml',
			status=EntradaNF.Status.AGUARDANDO,
			tipo=EntradaNF.Tipo.NORMAL,
		)
		arquivo = SimpleUploadedFile(
			'romaneio.xlsx',
			_build_minuta_workbook([
				('1', '55764', 'AUTO POSTO', 'AUTO POSTO', 'Cliente Novo', 'Cliente Novo', '5081690', '123', '9999924701', 'MXS_15', '21.94', '0', '1644.68'),
			]),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)

		response = self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo}, follow=True)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Importação da minuta concluída')
		self.assertContains(response, 'AGUARDANDO LIBERACAO')
		self.assertContains(response, 'Cliente Fluxo Autorizado')
		self.assertContains(response, 'Centro')
		item = MinutaRomaneioItem.objects.get(romaneio__codigo_romaneio='5081690', numero_nota='123')
		self.assertIsNone(item.nf_id)
		self.assertEqual(item.status, 'AGUARDANDO LIBERACAO')
		self.assertEqual(item.bairro, 'Centro')

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_upload_minuta_importa_nf_liberada_sem_notafiscal(self):
		EntradaNF.objects.create(
			chave_nf='35260400846804000106550010000001231027966311',
			numero_nf='123',
			xml='xmls/xml_autorizado.xml',
			status=EntradaNF.Status.LIBERADO,
			tipo=EntradaNF.Tipo.NORMAL,
		)
		arquivo = SimpleUploadedFile(
			'romaneio.xlsx',
			_build_minuta_workbook([
				('1', '55764', 'AUTO POSTO', 'AUTO POSTO', 'Cliente Novo', 'Cliente Novo', '5081690', '123', '9999924701', 'MXS_15', '21.94', '0', '1644.68'),
			]),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)

		response = self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo}, follow=True)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Importação da minuta concluída')
		self.assertContains(response, 'LIBERADA')
		item = MinutaRomaneioItem.objects.get(romaneio__codigo_romaneio='5081690', numero_nota='123')
		self.assertIsNone(item.nf_id)
		self.assertEqual(item.status, 'LIBERADA')

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_upload_minuta_bloqueia_nf_bloqueada(self):
		self.nf.bloqueada = True
		self.nf.status = NotaFiscal.Status.BLOQUEADA_COM_RESTRICAO
		self.nf.save(update_fields=['bloqueada', 'status', 'updated_at'])

		arquivo = SimpleUploadedFile(
			'romaneio.xlsx',
			_build_minuta_workbook([
				('1', '29664', 'TRANSPORTES LUCAS', 'TRANSPORTES LUCAS', 'Cliente Minuta', 'Cliente Minuta', '5081690', self.nf.numero, '9999924589', 'MXS_15', '57.6', '0', '2186.36'),
			]),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)

		response = self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo}, follow=True)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'NF BLOQUEADA')
		self.assertContains(response, 'A importação atual substituiu a versão anterior')
		self.assertTrue(MinutaRomaneio.objects.filter(codigo_romaneio='5081690').exists())

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_upload_minuta_bloqueia_nf_cancelada(self):
		self.nf.status_fiscal = NotaFiscal.StatusFiscal.CANCELADA
		self.nf.ativa = False
		self.nf.bloqueada = True
		self.nf.status = NotaFiscal.Status.BLOQUEADA_COM_RESTRICAO
		self.nf.save(update_fields=['status_fiscal', 'ativa', 'bloqueada', 'status', 'updated_at'])

		arquivo = SimpleUploadedFile(
			'romaneio.xlsx',
			_build_minuta_workbook([
				('1', '29664', 'TRANSPORTES LUCAS', 'TRANSPORTES LUCAS', 'Cliente Minuta', 'Cliente Minuta', '5081690', self.nf.numero, '9999924589', 'MXS_15', '57.6', '0', '2186.36'),
			]),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)

		response = self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo}, follow=True)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'NF CANCELADA')
		self.assertContains(response, 'A importação atual substituiu a versão anterior')
		self.assertTrue(MinutaRomaneio.objects.filter(codigo_romaneio='5081690').exists())

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_reimportacao_mesmo_romaneio_atualiza_cabecalho_e_sincroniza_itens(self):
		arquivo_inicial = SimpleUploadedFile(
			'romaneio_inicial.xlsx',
			_build_minuta_workbook([
				('1', '29664', 'TRANSPORTES LUCAS', 'TRANSPORTES LUCAS', 'Cliente Minuta', 'Cliente Minuta', '5081690', self.nf.numero, '9999924589', 'MXS_15', '57.6', '0', '2186.36'),
				('2', '55764', 'AUTO POSTO', 'AUTO POSTO', 'Cliente Novo', 'Cliente Novo', '5081690', '1419999', '9999924701', 'MXS_15', '21.94', '0', '1644.68'),
			]),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)
		response_inicial = self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo_inicial}, follow=True)
		self.assertEqual(response_inicial.status_code, 200)

		arquivo_reimportado = SimpleUploadedFile(
			'romaneio_reimportado.xlsx',
			_build_minuta_workbook(
				[
					('1', '29664', 'TRANSPORTES LUCAS', 'TRANSPORTES LUCAS', 'Cliente Minuta', 'Cliente Minuta', '5081690', self.nf.numero, '9999924589', 'MXS_15', '57.6', '0', '2186.36'),
				],
				motorista='9001 - MOTORISTA NOVO',
				veiculo='99 - XYZ1A23/BRIDA',
			),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)

		response_reimportado = self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo_reimportado}, follow=True)

		self.assertEqual(response_reimportado.status_code, 200)
		self.assertContains(response_reimportado, 'Importação da minuta concluída')
		self.assertEqual(MinutaRomaneio.objects.filter(codigo_romaneio='5081690').count(), 1)
		romaneio = MinutaRomaneio.objects.get(codigo_romaneio='5081690')
		self.assertEqual(romaneio.motorista, 'MOTORISTA NOVO')
		self.assertEqual(romaneio.placa, 'XYZ1A23')
		self.assertTrue(MinutaRomaneioItem.objects.filter(romaneio=romaneio, numero_nota=self.nf.numero).exists())
		self.assertFalse(MinutaRomaneioItem.objects.filter(romaneio=romaneio, numero_nota='1419999').exists())

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_importacao_substitui_nf_fora_da_planilha_e_lista_somente_lote_importado(self):
		romaneio = MinutaRomaneio.objects.create(
			codigo_romaneio='5081690',
			filial='BRIDA',
			data_saida=timezone.datetime(2026, 5, 12).date(),
			placa='FTG6B24',
			motorista='Motorista Antigo',
			usuario_importacao=self.usuario,
		)
		MinutaRomaneioItem.objects.create(
			romaneio=romaneio,
			nf=None,
			numero_nota='1415057',
			status='NF NÃO LOCALIZADA',
			razao_social='NF fora do lote',
		)

		nf_dois = NotaFiscal.objects.create(
			chave_nfe='35111111111111111111550010000000011000000888',
			numero='1419998',
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.PENDENTE,
			data_emissao='2026-05-12T11:30:00-03:00',
			bairro='Bairro 2',
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=False,
			ativa=True,
		)
		NotaFiscalItem.objects.create(nf=nf_dois, produto=self.produto, quantidade='1.00')
		nf_tres = NotaFiscal.objects.create(
			chave_nfe='35111111111111111111550010000000011000000889',
			numero='1419997',
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.PENDENTE,
			data_emissao='2026-05-12T12:00:00-03:00',
			bairro='Bairro 3',
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=False,
			ativa=True,
		)
		NotaFiscalItem.objects.create(nf=nf_tres, produto=self.produto, quantidade='1.00')

		arquivo = SimpleUploadedFile(
			'romaneio_limpo.xlsx',
			_build_minuta_workbook([
				('1', '29664', 'CLIENTE 1', 'CLIENTE 1', 'Cliente Minuta', 'Cliente Minuta', '5081690', self.nf.numero, 'PED001', 'MXS_15', '57.6', '0', '2186.36'),
				('2', '29665', 'CLIENTE 2', 'CLIENTE 2', 'Cliente Minuta', 'Cliente Minuta', '5081690', '1419998', 'PED002', 'MXS_15', '21.94', '0', '1644.68'),
				('3', '29666', 'CLIENTE 3', 'CLIENTE 3', 'Cliente Minuta', 'Cliente Minuta', '5081690', '1419997', 'PED003', 'MXS_15', '30.00', '0', '999.00'),
			]),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)

		response = self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo}, follow=True)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Importação da minuta concluída')
		self.assertEqual(MinutaRomaneioItem.objects.filter(romaneio__codigo_romaneio='5081690').count(), 3)
		self.assertFalse(MinutaRomaneioItem.objects.filter(romaneio__codigo_romaneio='5081690', numero_nota='1415057').exists())

		linhas, _ = listar_minuta_itens(romaneio='5081690')
		self.assertEqual({linha['numero_nota'] for linha in linhas}, {self.nf.numero, '1419998', '1419997'})

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_reimportacao_do_mesmo_romaneio_remove_versao_antiga_com_data_anterior(self):
		nf_nova = NotaFiscal.objects.create(
			chave_nfe='35111111111111111111550010000000011000000123',
			numero='1411592',
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.PENDENTE,
			data_emissao='2026-05-13T11:00:00-03:00',
			bairro='Vila Constancia',
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=False,
			ativa=True,
		)
		NotaFiscalItem.objects.create(nf=nf_nova, produto=self.produto, quantidade='1.00')

		arquivo_inicial = SimpleUploadedFile(
			'romaneio_inicial.xlsx',
			_build_minuta_workbook([
				('1', '29664', 'CLIENTE 1', 'CLIENTE 1', 'Cliente Minuta', 'Cliente Minuta', '5081690', self.nf.numero, 'PED001', 'MXS_15', '57.6', '0', '2186.36'),
				('2', '29665', 'CLIENTE FORA', 'CLIENTE FORA', 'Cliente Minuta', 'Cliente Minuta', '5081690', '1415057', 'PED002', 'MXS_15', '21.94', '0', '1644.68'),
			]),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)
		response_inicial = self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo_inicial}, follow=True)
		self.assertEqual(response_inicial.status_code, 200)
		self.assertEqual(MinutaRomaneio.objects.filter(codigo_romaneio='5081690').count(), 1)
		self.assertTrue(MinutaRomaneioItem.objects.filter(romaneio__codigo_romaneio='5081690', numero_nota='1415057').exists())

		arquivo_reimportado = SimpleUploadedFile(
			'romaneio_reimportado.xlsx',
			_build_minuta_workbook(
				[
					('1', '29664', 'CLIENTE 1', 'CLIENTE 1', 'Cliente Minuta', 'Cliente Minuta', '5081690', self.nf.numero, 'PED001', 'MXS_15', '57.6', '0', '2186.36'),
					('2', '29665', 'CLIENTE 2', 'CLIENTE 2', 'Cliente Minuta', 'Cliente Minuta', '5081690', '1411592', 'PED003', 'MXS_15', '21.94', '0', '1644.68'),
				],
				data_saida='13/05/2026',
			),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)
		response_reimportado = self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo_reimportado}, follow=True)

		self.assertEqual(response_reimportado.status_code, 200)
		self.assertContains(response_reimportado, 'Importação da minuta concluída')
		self.assertEqual(MinutaRomaneio.objects.filter(codigo_romaneio='5081690').count(), 1)
		romaneio = MinutaRomaneio.objects.get(codigo_romaneio='5081690')
		self.assertEqual(romaneio.data_saida, timezone.datetime(2026, 5, 13).date())
		self.assertEqual(set(MinutaRomaneioItem.objects.filter(romaneio=romaneio).values_list('numero_nota', flat=True)), {self.nf.numero, '1411592'})
		self.assertFalse(MinutaRomaneioItem.objects.filter(romaneio__codigo_romaneio='5081690', numero_nota='1415057').exists())

		linhas, _ = listar_minuta_itens(romaneio='5081690')
		self.assertEqual({linha['numero_nota'] for linha in linhas}, {self.nf.numero, '1411592'})

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_listagem_padrao_mostra_apenas_lote_ativo_da_ultima_importacao(self):
		romaneio_antigo = MinutaRomaneio.objects.create(
			codigo_romaneio='5081691',
			filial='BRIDA',
			data_saida=timezone.datetime(2026, 5, 12).date(),
			placa='FTG6B24',
			motorista='CLAUDIO SOUZA DE JESUS',
			usuario_importacao=self.usuario,
		)
		MinutaRomaneioItem.objects.create(
			romaneio=romaneio_antigo,
			numero_nota='1415057',
			status='NF NÃO LOCALIZADA',
			razao_social='MANOEL MESSIAS SOARES FEITOSA',
		)

		nf_dois = NotaFiscal.objects.create(
			chave_nfe='35111111111111111111550010000000011000000124',
			numero='1411589',
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.PENDENTE,
			data_emissao='2026-05-12T11:00:00-03:00',
			bairro='Parque Rincao',
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=False,
			ativa=True,
		)
		NotaFiscalItem.objects.create(nf=nf_dois, produto=self.produto, quantidade='1.00')
		nf_tres = NotaFiscal.objects.create(
			chave_nfe='35111111111111111111550010000000011000000125',
			numero='1411593',
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.PENDENTE,
			data_emissao='2026-05-12T12:00:00-03:00',
			bairro='Cidade Jardim Cumbica',
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=False,
			ativa=True,
		)
		NotaFiscalItem.objects.create(nf=nf_tres, produto=self.produto, quantidade='1.00')

		arquivo = SimpleUploadedFile(
			'romaneio_ativo.xlsx',
			_build_minuta_workbook([
				('1', '29664', 'ZEUS', 'ZEUS', 'ZEUS TRANSPORTES E ORGANIZACAO LOGISTICA LTDA', 'ZEUS TRANSPORTES E ORGANIZACAO LOGISTICA LTDA', '5081690', '1411589', 'PED001', 'MXS_15', '57.6', '0', '2186.36'),
				('2', '29665', 'AUTO POSTO', 'AUTO POSTO', 'AUTO POSTO BRAZAO LTDA', 'AUTO POSTO BRAZAO LTDA', '5081690', '1411592', 'PED002', 'MXS_15', '21.94', '0', '1644.68'),
				('3', '29666', 'MOTOPARTSS', 'MOTOPARTSS', 'MOTOPARTSS COMERCIO DE PECAS LTDA', 'MOTOPARTSS COMERCIO DE PECAS LTDA', '5081690', '1411593', 'PED003', 'MXS_15', '30.00', '0', '999.00'),
			]),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)

		response = self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo}, follow=True)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '5081690')
		self.assertContains(response, '1411589')
		self.assertContains(response, '1411592')
		self.assertContains(response, '1411593')
		self.assertNotContains(response, '5081691')
		self.assertNotContains(response, '1415057')

		linhas, resumo = listar_minuta_itens()
		self.assertEqual({linha['romaneio'] for linha in linhas}, {'5081690'})
		self.assertEqual({linha['numero_nota'] for linha in linhas}, {'1411589', '1411592', '1411593'})
		self.assertEqual(resumo['itens'], 3)

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_pdf_minuta_usa_apenas_lote_ativo(self):
		romaneio_antigo = MinutaRomaneio.objects.create(
			codigo_romaneio='5081691',
			filial='BRIDA',
			data_saida=timezone.datetime(2026, 5, 12).date(),
			placa='FTG6B24',
			motorista='CLAUDIO ANTIGO',
			usuario_importacao=self.usuario,
		)
		MinutaRomaneioItem.objects.create(
			romaneio=romaneio_antigo,
			numero_nota='1415057',
			status='NF NÃO LOCALIZADA',
			razao_social='CLIENTE ANTIGO',
		)

		nf_dois = NotaFiscal.objects.create(
			chave_nfe='35111111111111111111550010000000011000000126',
			numero='1411589',
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.PENDENTE,
			data_emissao='2026-05-12T11:00:00-03:00',
			bairro='Parque Rincao',
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=False,
			ativa=True,
		)
		NotaFiscalItem.objects.create(nf=nf_dois, produto=self.produto, quantidade='1.00')
		nf_tres = NotaFiscal.objects.create(
			chave_nfe='35111111111111111111550010000000011000000127',
			numero='1411593',
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.PENDENTE,
			data_emissao='2026-05-12T12:00:00-03:00',
			bairro='Cidade Jardim Cumbica',
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=False,
			ativa=True,
		)
		NotaFiscalItem.objects.create(nf=nf_tres, produto=self.produto, quantidade='1.00')

		arquivo = SimpleUploadedFile(
			'romaneio_pdf.xlsx',
			_build_minuta_workbook([
				('1', '29664', 'ZEUS', 'ZEUS', 'ZEUS TRANSPORTES E ORGANIZACAO LOGISTICA LTDA', 'ZEUS TRANSPORTES E ORGANIZACAO LOGISTICA LTDA', '5081690', '1411589', 'PED001', 'MXS_15', '57.6', '0.100', '2186.36'),
				('2', '29665', 'AUTO POSTO', 'AUTO POSTO', 'AUTO POSTO BRAZAO LTDA', 'AUTO POSTO BRAZAO LTDA', '5081690', self.nf.numero, 'PED002', 'MXS_15', '21.94', '0.050', '1644.68'),
				('3', '29666', 'MOTOPARTSS', 'MOTOPARTSS', 'MOTOPARTSS COMERCIO DE PECAS LTDA', 'MOTOPARTSS COMERCIO DE PECAS LTDA', '5081690', '1411593', 'PED003', 'MXS_15', '30.00', '0.070', '999.00'),
			]),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)
		self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo}, follow=True)

		response = self.client.get('/minuta/pdf/')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response['Content-Type'], 'application/pdf')
		self.assertIn('minuta_carregamento_5081690.pdf', response['Content-Disposition'])
		self.assertTrue(response.content.startswith(b'%PDF'))
		self.assertIn(b'MINUTA DE CARREGAMENTO', response.content)
		self.assertIn(b'Nota', response.content)
		self.assertIn(b'Emissao', response.content)
		self.assertIn(b'Cliente', response.content)
		self.assertIn(b'Qtd', response.content)
		self.assertIn(b'Peso', response.content)
		self.assertIn(b'ROTA: CAIEIRAS', response.content)
		self.assertIn(b'MOBIL SUPER 3000 5W30 24X1L', response.content)
		self.assertIn(b'MIN001', response.content)
		self.assertIn(b'CX', response.content)
		self.assertIn(b'(1)', response.content.replace(b'\n', b''))
		self.assertIn(b'(57,60)', response.content.replace(b'\n', b''))
		self.assertNotIn(b'Sem itens de produto vinculados', response.content)
		self.assertNotIn(b'NF CLIENTE BAIRRO STATUS PESO KG VOL M3 VALOR', response.content)
		self.assertIn(b'5081690', response.content)
		self.assertIn(b'1411589', response.content)
		self.assertIn(self.nf.numero.encode(), response.content)
		self.assertIn(b'1411593', response.content)
		self.assertLess(response.content.index(b'1411593'), response.content.index(self.nf.numero.encode()))
		self.assertLess(response.content.index(self.nf.numero.encode()), response.content.index(b'1411589'))
		self.assertNotIn(b'5081691', response.content)
		self.assertNotIn(b'1415057', response.content)

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_pdf_minuta_resolve_itens_por_numero_quando_item_minuta_esta_sem_nf(self):
		arquivo = SimpleUploadedFile(
			'romaneio_fallback.xlsx',
			_build_minuta_workbook([
				('1', '29664', 'CLIENTE 1', 'CLIENTE 1', 'Cliente Minuta', 'Cliente Minuta', '5081690', self.nf.numero, 'PED001', 'MXS_15', '57.6', '0.100', '2186.36'),
			]),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)

		response_upload = self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo}, follow=True)
		self.assertEqual(response_upload.status_code, 200)

		MinutaRomaneioItem.objects.filter(numero_nota=self.nf.numero).update(nf=None)

		response = self.client.get('/minuta/pdf/')

		self.assertEqual(response.status_code, 200)
		self.assertIn(self.nf.numero.encode(), response.content)
		self.assertIn(b'MIN001', response.content)
		self.assertIn(b'MOBIL SUPER 3000 5W30 24X1L', response.content)
		self.assertIn(b'Qtd', response.content)
		self.assertNotIn(b'XML nao localizado', response.content)

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_pdf_minuta_usa_itens_do_xml_quando_nf_nao_existe_no_banco(self):
		numero_nf = '1418888'
		chave_nf = '35260500846804000106550010014188881000000001'
		EntradaNF.objects.create(
			chave_nf=chave_nf,
			numero_nf=numero_nf,
			xml=SimpleUploadedFile(
				f'{chave_nf}.xml',
				_build_nfe_xml(
					numero_nf,
					chave_nf,
					[
						{'codigo': '123943', 'descricao': 'MOBIL SUPER 3000 5W30 24X1L', 'quantidade': '1.0000', 'unidade': 'CX'},
						{'codigo': '999001', 'descricao': 'FILTRO LUBRIFICANTE', 'quantidade': '2.0000', 'unidade': 'UN'},
					],
				),
				content_type='application/xml',
			),
		)

		arquivo = SimpleUploadedFile(
			'romaneio_xml.xlsx',
			_build_minuta_workbook([
				('1', '29664', 'CLIENTE XML', 'CLIENTE XML', 'CLIENTE XML LTDA', 'CLIENTE XML LTDA', '5081690', numero_nf, 'PED001', 'MXS_15', '57.6', '0.100', '2186.36'),
			]),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)

		response_upload = self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo}, follow=True)
		self.assertEqual(response_upload.status_code, 200)

		response = self.client.get('/minuta/pdf/')

		self.assertEqual(response.status_code, 200)
		self.assertIn(b'ROTA: CAIEIRAS', response.content)
		self.assertIn(b'Qtd', response.content)
		self.assertIn(b'MOBIL SUPER 3000 5W30 24X1L', response.content)
		self.assertIn(b'FILTRO LUBRIFICANTE', response.content)
		self.assertIn(b'123943', response.content)
		self.assertIn(b'CX', response.content)
		self.assertIn(b'(3)', response.content)
		self.assertIn(b'(1)', response.content.replace(b'\n', b''))
		self.assertIn(b'(38,40)', response.content.replace(b'\n', b''))
		self.assertLess(response.content.index(b'FILTRO LUBRIFICANTE'), response.content.index(b'MOBIL SUPER 3000 5W30 24X1L'))
		self.assertNotIn(b'0,000', response.content)
		self.assertNotIn(b'XML localizado sem itens processados', response.content)

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_pdf_minuta_limpa_texto_fiscal_da_rota(self):
		numero_nf = '1417777'
		chave_nf = '35260500846804000106550010014177771000000001'
		EntradaNF.objects.create(
			chave_nf=chave_nf,
			numero_nf=numero_nf,
			xml=SimpleUploadedFile(
				f'{chave_nf}.xml',
				_build_nfe_xml(
					numero_nf,
					chave_nf,
					[{'codigo': '123075', 'descricao': 'PI: MOBIL SUPER MOTO 4T MX 10W30 24X1L', 'quantidade': '3.0000', 'unidade': 'CX'}],
						inf_cpl='Pedido teste - Rota: ITAPEVI\\NTRIB APROX. R$ 240,89 FED, 71,64 EST E 0,00 MUN FONTE: IBPT',
				),
				content_type='application/xml',
			),
		)

		arquivo = SimpleUploadedFile(
			'romaneio_rota_limpa.xlsx',
			_build_minuta_workbook([
				('1', '29664', 'CLIENTE ROTA', 'CLIENTE ROTA', 'CLIENTE ROTA LTDA', 'CLIENTE ROTA LTDA', '5081690', numero_nf, 'PED001', 'MXS_15', '57.6', '0.100', '2186.36'),
			]),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)

		self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo}, follow=True)
		response = self.client.get('/minuta/pdf/')

		self.assertEqual(response.status_code, 200)
		self.assertIn(b'ROTA: ITAPEVI', response.content)
		self.assertNotIn(b'ITAPEVI\\N', response.content)
		self.assertNotIn(b'TRIB APROX', response.content)
		self.assertNotIn(b'FED', response.content)
		self.assertNotIn(b'EST', response.content)
		self.assertNotIn(b'MUN', response.content)
		self.assertNotIn(b'FONTE', response.content)
		self.assertNotIn(b'VALOR CBS', response.content)
		self.assertNotIn(b'VALOR IBS', response.content)

	def test_pdf_minuta_restringe_acesso_para_conferente(self):
		usuario_conferente = Usuario.objects.create_user(
			username='conferente_minuta_pdf',
			nome='Conferente Minuta',
			perfil=Usuario.Perfil.CONFERENTE,
			setores=[Setor.Codigo.FILTROS],
			password='123456',
			is_active=True,
		)
		self.client.force_login(usuario_conferente)

		response = self.client.get('/minuta/pdf/')

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.headers['Location'], '/conferencia/')

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_pdf_minuta_entrega_gera_pdf_individual(self):
		arquivo = SimpleUploadedFile(
			'romaneio_entrega.xlsx',
			_build_minuta_workbook([
				('1', '29664', 'CLIENTE 1', 'CLIENTE 1', 'Cliente Minuta', 'Cliente Minuta', '5081690', self.nf.numero, 'PED001', 'MXS_15', '57.6', '1', '2186.36'),
			]),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)

		self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo}, follow=True)
		response = self.client.get('/minuta/pdf/?carregamento=0&entrega=1')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response['Content-Type'], 'application/pdf')
		self.assertIn('minuta_entrega_5081690.pdf', response['Content-Disposition'])
		self.assertIn(b'MINUTA DE ENTREGA', response.content)
		self.assertNotIn(b'Total Volumes:', response.content)
		self.assertIn(b'Total Valor:', response.content)
		self.assertIn(b'Carregamento: 5081690', response.content)
		self.assertIn(b'Cliente Minuta', response.content)

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_pdf_minuta_gera_zip_quando_carregamento_e_entrega_estao_marcados(self):
		arquivo = SimpleUploadedFile(
			'romaneio_zip.xlsx',
			_build_minuta_workbook([
				('1', '29664', 'CLIENTE 1', 'CLIENTE 1', 'Cliente Minuta', 'Cliente Minuta', '5081690', self.nf.numero, 'PED001', 'MXS_15', '57.6', '1', '2186.36'),
			]),
			content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
		)

		self.client.post('/minuta/', {'acao': 'upload', 'arquivo': arquivo}, follow=True)
		response = self.client.get('/minuta/pdf/?carregamento=1&entrega=1')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response['Content-Type'], 'application/zip')
		self.assertIn('minutas_5081690.zip', response['Content-Disposition'])
		with zipfile.ZipFile(io.BytesIO(response.content), 'r') as arquivo_zip:
			self.assertEqual(
				set(arquivo_zip.namelist()),
				{'minuta_carregamento_5081690.pdf', 'minuta_entrega_5081690.pdf'},
			)
			self.assertTrue(arquivo_zip.read('minuta_carregamento_5081690.pdf').startswith(b'%PDF'))
			self.assertTrue(arquivo_zip.read('minuta_entrega_5081690.pdf').startswith(b'%PDF'))

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_dashboard_separacao_usa_atualizacao_manual(self):
		response = self.client.get('/dashboard/separacao/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Atualizar')
		self.assertNotContains(response, 'dashboardRefreshIntervalMs', html=False)
		self.assertNotContains(response, 'setTimeout(cicloAtualizacao', html=False)
		self.assertNotContains(response, 'fetch(`/api/dashboard/resumo/', html=False)

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_dashboard_separacao_nao_exibe_exclusao_para_tarefa_concluida_com_restricao(self):
		self.tarefa.status = Tarefa.Status.CONCLUIDO_COM_RESTRICAO
		self.tarefa.save(update_fields=['status', 'updated_at'])
		TarefaItem.objects.filter(tarefa=self.tarefa, produto=self.produto_pendente).update(
			possui_restricao=False,
			quantidade_separada=0,
		)

		response = self.client.get('/dashboard/separacao/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'CONCLUIDO COM RESTRICAO')
		self.assertNotContains(response, f'data-exclusao-url="/tarefas/excluir/{self.tarefa.id}/"', html=False)

	@override_settings(
		STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
		STORAGES={
			'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
			'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
		},
	)
	def test_dashboard_conferencia_usa_atualizacao_manual(self):
		response = self.client.get('/dashboard/conferencia/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Atualizar')
		self.assertNotContains(response, 'dashboardRefreshIntervalMs', html=False)
		self.assertNotContains(response, 'setTimeout(cicloAtualizacao', html=False)
		self.assertNotContains(response, 'fetch(`/api/dashboard/resumo/', html=False)

	def test_conferencia_lista_usa_atualizacao_manual(self):
		response = self.client.get('/conferencia/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Atualizar')
		self.assertNotContains(response, 'setTimeout(cicloAtualizacao', html=False)
		self.assertNotContains(response, 'fetch(window.location.href', html=False)

	def test_separacao_lista_ajax_retorna_somente_tabela(self):
		response = self.client.get('/separacao/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '<table', html=False)
		self.assertNotContains(response, '<html', html=False)

	def test_conferencia_lista_ajax_retorna_somente_tabela(self):
		response = self.client.get('/conferencia/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '<table', html=False)
		self.assertNotContains(response, '<html', html=False)

	def test_dashboard_separacao_ajax_partial_retorna_apenas_tabela(self):
		response = self.client.get('/dashboard/separacao/?partial=table', HTTP_X_REQUESTED_WITH='XMLHttpRequest')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Cod separação')
		self.assertNotContains(response, 'Dashboard de Separação')

	def test_dashboard_conferencia_ajax_partial_retorna_apenas_tabela(self):
		response = self.client.get('/dashboard/conferencia/?partial=table', HTTP_X_REQUESTED_WITH='XMLHttpRequest')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '<table', html=False)
		self.assertNotContains(response, 'Dashboard de Conferência')

	def test_dashboard_separacao_respeita_filtro_de_periodo(self):
		data_com_dados = self.tarefa.created_at.date()
		data_sem_dados = data_com_dados - timedelta(days=1)
		response = self.client.get(f'/dashboard/separacao/?data_inicial={data_sem_dados.isoformat()}&data_final={data_sem_dados.isoformat()}')
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Nenhum item de separação encontrado.')

		response_no_dia_correto = self.client.get(f'/dashboard/separacao/?data_inicial={data_com_dados.isoformat()}&data_final={data_com_dados.isoformat()}')
		self.assertEqual(response_no_dia_correto.status_code, 200)
		self.assertContains(response_no_dia_correto, '1410289')

	def test_dashboard_separacao_sem_filtro_usa_data_de_hoje(self):
		data_antiga = timezone.now() - timedelta(days=3)
		Tarefa.objects.filter(id=self.tarefa.id).update(created_at=data_antiga, updated_at=data_antiga)
		NotaFiscal.objects.filter(id=self.nf.id).update(data_emissao=data_antiga)

		response = self.client.get('/dashboard/separacao/')

		self.assertEqual(response.status_code, 200)
		hoje = timezone.localdate().isoformat()
		self.assertEqual(response.context['filtros']['date_from'], hoje)
		self.assertEqual(response.context['filtros']['date_to'], hoje)
		self.assertContains(response, 'Nenhum item de separação encontrado.')
		self.assertEqual(response.context['indicadores']['total'], 0)

	def test_dashboard_conferencia_respeita_filtro_de_periodo(self):
		data_com_dados = self.nf.created_at.date()
		data_sem_dados = data_com_dados - timedelta(days=1)
		response = self.client.get(f'/dashboard/conferencia/?data_inicial={data_sem_dados.isoformat()}&data_final={data_sem_dados.isoformat()}')
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Nenhuma NF encontrada.')

		response_no_dia_correto = self.client.get(f'/dashboard/conferencia/?data_inicial={data_com_dados.isoformat()}&data_final={data_com_dados.isoformat()}')
		self.assertEqual(response_no_dia_correto.status_code, 200)
		self.assertContains(response_no_dia_correto, 'name="data_inicial"', html=False)
		self.assertContains(response_no_dia_correto, 'name="data_final"', html=False)

	def test_dashboard_conferencia_sem_filtro_usa_data_de_hoje(self):
		data_antiga = timezone.now() - timedelta(days=3)
		NotaFiscal.objects.filter(id=self.nf.id).update(created_at=data_antiga, data_emissao=data_antiga, updated_at=data_antiga)
		Conferencia.objects.filter(id=self.conferencia.id).update(created_at=data_antiga, updated_at=data_antiga)

		response = self.client.get('/dashboard/conferencia/')

		self.assertEqual(response.status_code, 200)
		hoje = timezone.localdate().isoformat()
		self.assertEqual(response.context['filtros']['date_from'], hoje)
		self.assertEqual(response.context['filtros']['date_to'], hoje)
		self.assertContains(response, 'Nenhuma NF encontrada.')

	def test_dashboard_conferencia_marca_nf_como_concluida_quando_itens_estao_totalmente_conferidos(self):
		item_tarefa = TarefaItem.objects.get(tarefa=self.tarefa, produto=self.produto_pendente)
		item_tarefa.quantidade_separada = '5.00'
		item_tarefa.save(update_fields=['quantidade_separada'])

		item_conferencia = ConferenciaItem.objects.get(conferencia=self.conferencia, produto=self.produto_pendente)
		item_conferencia.qtd_conferida = '5.00'
		item_conferencia.status = ConferenciaItem.Status.OK
		item_conferencia.save(update_fields=['qtd_conferida', 'status', 'updated_at'])

		response = self.client.get('/dashboard/conferencia/')
		self.nf.refresh_from_db()

		self.assertEqual(response.status_code, 200)
		self.assertIn(self.nf.status, {NotaFiscal.Status.CONCLUIDO, NotaFiscal.Status.EM_CONFERENCIA})
		self.assertContains(response, 'Dashboard de Conferência')

	def test_dashboard_conferencia_mantem_nf_no_historico_quando_conferencia_concluida(self):
		self.conferencia.status = Conferencia.Status.OK
		self.conferencia.save(update_fields=['status', 'updated_at'])

		response = self.client.get('/dashboard/conferencia/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '1410289')
		self.assertContains(response, 'OK')

	def test_dashboard_conferencia_detalhe_nf_exibe_historico_de_separacao_quando_nao_ha_bipagem_conferencia(self):
		item_sep = TarefaItem.objects.filter(tarefa=self.tarefa, produto=self.produto_ok).first()
		item_sep.bipado_por = self.usuario
		item_sep.data_bipagem = timezone.now()
		item_sep.save(update_fields=['bipado_por', 'data_bipagem', 'updated_at'])

		response = self.client.get('/dashboard/conferencia/?nf_detalhe=1410289')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Detalhe da NF 1410289')
		self.assertContains(response, self.produto_ok.cod_prod)
		self.assertContains(response, self.usuario.nome)

	def test_dashboard_conferencia_detalhe_nf_ignora_filtro_setor_para_rastreabilidade(self):
		self.usuario.setores.clear()
		item_sep = TarefaItem.objects.filter(tarefa=self.tarefa, produto=self.produto_ok).first()
		item_sep.bipado_por = self.usuario
		item_sep.data_bipagem = timezone.now()
		item_sep.save(update_fields=['bipado_por', 'data_bipagem', 'updated_at'])

		response = self.client.get('/dashboard/conferencia/?nf_detalhe=1410289')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Detalhe da NF 1410289')

	def test_dashboard_separacao_exibe_nf_de_item_consolidado(self):
		response = self.client.get('/dashboard/separacao/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '1410289')
		self.assertContains(response, 'Rodrigo')

	@patch('apps.conferencia.views_web.listar_nfs_disponiveis')
	def test_gestor_ve_lupa_nas_listas_de_conferencia_e_separacao(self, mock_listar_nfs):
		mock_listar_nfs.return_value = [
			{
				'id': self.nf.id,
				'numero': self.nf.numero,
				'cliente': self.cliente.nome,
				'rota': self.rota.nome,
				'status': 'PENDENTE',
				'status_separacao': 'SEPARADO',
				'conferencia_liberada': True,
				'conferencia_bloqueio_motivo': '',
				'balcao': False,
				'progresso': {'conferido': 0, 'esperado': 2},
				'itens_pendentes_conferencia': 2,
				'bloqueado': False,
				'usuario_em_uso': '',
				'em_uso_por_mim': False,
			}
		]

		response_conferencia = self.client.get('/conferencia/')
		response_separacao = self.client.get('/separacao/')

		self.assertEqual(response_conferencia.status_code, 200)
		self.assertEqual(response_separacao.status_code, 200)
		self.assertContains(response_conferencia, 'action-icon--detalhe')
		self.assertContains(response_separacao, 'action-icon--detalhe')
		self.assertContains(response_separacao, 'Imprimir minuta')

	def test_impressao_minuta_separacao_retorna_pdf_com_dados_da_tarefa(self):
		response = self.client.get(f'/separacao/{self.tarefa.id}/imprimir/')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response['Content-Type'], 'application/pdf')
		self.assertIn('minuta-separacao-1.pdf', response['Content-Disposition'])
		self.assertTrue(response.content.startswith(b'%PDF'))
		self.assertIn(b'MINUTA DE SEPARACAO', response.content)
		self.assertIn(b'1410289', response.content)
		self.assertIn(b'Rodrigo', response.content)
		self.assertIn(b'L01', response.content)
		self.assertIn(b'FILTROS', response.content)
		self.assertIn(b'123223', response.content)
		self.assertIn(b'123039', response.content)
		self.assertIn(b'TOTAL QTDE:', response.content)
		self.assertIn(b'15', response.content)
		self.assertNotIn(b'( 3) Tj', response.content)
		self.assertNotIn(b'( 10) Tj', response.content)

	def test_impressao_minuta_separacao_respeita_permissao_por_setor(self):
		self.client.force_login(self.usuario_conferente)

		response = self.client.get(f'/separacao/{self.tarefa.id}/imprimir/')

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.headers['Location'], '/conferencia/')

	def test_separador_nao_pode_gerar_minuta_por_url_direta(self):
		self.client.force_login(self.usuario_separador)

		response = self.client.get(f'/separacao/{self.tarefa.id}/imprimir/')

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.headers['Location'], '/separacao/')

	def test_conferente_nao_ve_lupa_na_lista_de_conferencia(self):
		self.client.force_login(self.usuario_conferente)

		response = self.client.get('/conferencia/')

		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, 'action-icon--detalhe')
		self.assertNotContains(response, 'web-conferencia-detalhe')

	def test_separador_nao_ve_lupa_na_lista_de_separacao(self):
		self.client.force_login(self.usuario_separador)

		response = self.client.get('/separacao/')

		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, 'action-icon--detalhe')
		self.assertNotContains(response, 'web-conferencia-detalhe')
		self.assertNotContains(response, 'Imprimir minuta')

	def test_conferente_nao_ve_botao_minuta_na_lista_separacao(self):
		self.client.force_login(self.usuario_conferente)

		response = self.client.get('/separacao/')

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.headers['Location'], '/conferencia/')

	def test_detalhe_nf_exibe_pendencia_de_separacao(self):
		response = self.client.get(f'/conferencia/detalhe/{self.nf.id}/')

		self.assertEqual(response.status_code, 200)

	def test_conferente_nao_acessa_detalhe_nf_por_url_direta(self):
		self.client.force_login(self.usuario_conferente)

		response = self.client.get(f'/conferencia/detalhe/{self.nf.numero}/')

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.headers['Location'], '/conferencia/')

	def test_separador_nao_acessa_detalhe_nf_por_url_direta(self):
		self.client.force_login(self.usuario_separador)

		response = self.client.get(f'/conferencia/detalhe/{self.nf.numero}/')

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.headers['Location'], '/separacao/')

	def test_status_nf_api_retorna_itens_e_status(self):
		response = self.client.get(f'/api/status/nf/{self.nf.id}/')

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertEqual(payload['nf_status'], 'EM_CONFERENCIA')
		self.assertEqual(len(payload['itens']), 2)
		self.assertEqual(payload['itens'][0]['produto'], '123223')
		self.assertIn('esperado', payload['itens'][0])
		self.assertEqual(payload['itens'][1]['status'], 'FALTA SEPARAR')

	def test_detalhe_nf_exibe_aguardando_quando_separado_mas_ainda_falta_conferir(self):
		item_tarefa = TarefaItem.objects.get(tarefa=self.tarefa, produto=self.produto_pendente)
		item_tarefa.quantidade_separada = '5.00'
		item_tarefa.save(update_fields=['quantidade_separada'])

		response = self.client.get(f'/conferencia/detalhe/{self.nf.id}/')

		self.assertEqual(response.status_code, 200)

	def test_status_nf_api_retorna_aguardando_quando_falta_apenas_conferencia(self):
		item_tarefa = TarefaItem.objects.get(tarefa=self.tarefa, produto=self.produto_pendente)
		item_tarefa.quantidade_separada = '5.00'
		item_tarefa.save(update_fields=['quantidade_separada'])

		response = self.client.get(f'/api/status/nf/{self.nf.id}/')

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		item_pendente = next(item for item in payload['itens'] if item['produto'] == '123039')
		self.assertEqual(item_pendente['status'], 'AGUARDANDO')

	def test_status_nf_api_retorna_concluido_quando_todos_itens_estao_conferidos(self):
		item_tarefa = TarefaItem.objects.get(tarefa=self.tarefa, produto=self.produto_pendente)
		item_tarefa.quantidade_separada = '5.00'
		item_tarefa.save(update_fields=['quantidade_separada'])

		item_conferencia = ConferenciaItem.objects.get(conferencia=self.conferencia, produto=self.produto_pendente)
		item_conferencia.qtd_conferida = '5.00'
		item_conferencia.status = ConferenciaItem.Status.OK
		item_conferencia.save(update_fields=['qtd_conferida', 'status', 'updated_at'])

		response = self.client.get(f'/api/status/nf/{self.nf.id}/')

		self.assertEqual(response.status_code, 200)
		self.assertIn(response.json()['nf_status'], {'CONCLUIDO', 'EM_CONFERENCIA'})

	def test_status_tarefa_api_retorna_quantidade_e_status(self):
		response = self.client.get(f'/api/status/tarefa/{self.tarefa.id}/')

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertEqual(payload['tarefa_id'], self.tarefa.id)
		self.assertEqual(payload['status'], Tarefa.Status.ABERTO)
		self.assertEqual(len(payload['itens']), 2)
		itens_por_produto = {item['produto']: item for item in payload['itens']}
		self.assertEqual(itens_por_produto['123039']['quantidade_separada'], 3.0)


	def test_dashboard_resumo_api_retorna_indicadores_agregados(self):
		data_com_dados = self.tarefa.created_at.date().isoformat()
		response = self.client.get(f'/api/dashboard/resumo/?data_inicial={data_com_dados}&data_final={data_com_dados}')

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertGreaterEqual(payload['total'], 0)
		self.assertGreaterEqual(payload['separado'], 0)
		self.assertIn('total_nfs', payload)
		self.assertIn('em_conferencia', payload)

	def test_dashboard_resumo_api_conta_nf_conferida_no_monitoramento(self):
		self.conferencia.status = Conferencia.Status.OK
		self.conferencia.save(update_fields=['status', 'updated_at'])
		data = self.conferencia.created_at.date().isoformat()

		response = self.client.get(f'/api/dashboard/resumo/?data_inicial={data}&data_final={data}')

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertGreaterEqual(payload['total_nfs'], 1)
		self.assertGreaterEqual(payload['conferidas'], 1)

	def test_dashboard_home_api_conta_nf_com_pendencia_real_de_conferencia_via_item_tarefa(self):
		self.usuario.definir_setores([Setor.Codigo.LUBRIFICANTE])
		self.produto_ok.categoria = Produto.Categoria.LUBRIFICANTE
		self.produto_ok.save(update_fields=['categoria'])
		self.produto_pendente.categoria = Produto.Categoria.LUBRIFICANTE
		self.produto_pendente.save(update_fields=['categoria'])
		self.tarefa.setor = Setor.Codigo.LUBRIFICANTE
		self.tarefa.save(update_fields=['setor', 'updated_at'])
		self.tarefa.itens.update(quantidade_separada=F('quantidade_total'))
		self.tarefa.status = Tarefa.Status.CONCLUIDO
		self.tarefa.save(update_fields=['status', 'updated_at'])

		response = self.client.get('/home/')

		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, 'Resumo do dia')
		self.assertContains(response, 'Controle operacional')

	def test_tela_separacao_contém_script_de_polling(self):
		self.tarefa.status = Tarefa.Status.EM_EXECUCAO
		self.tarefa.usuario = self.usuario
		self.tarefa.usuario_em_execucao = self.usuario
		self.tarefa.save(update_fields=['status', 'usuario', 'usuario_em_execucao', 'updated_at'])

		response = self.client.get(f'/separacao/{self.tarefa.id}/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '/api/tarefa-status/')
		self.assertContains(response, '/api/separacao/bipar/')
		self.assertContains(response, 'pollingIntervalMs = 10000', html=False)
		self.assertContains(response, 'visibilitychange', html=False)
		self.assertContains(response, 'pagehide', html=False)
		self.assertContains(response, 'clearInterval', html=False)
		self.assertContains(response, 'scheduleInputFocus', html=False)
		self.assertContains(response, 'codigoInput.focus({ preventScroll: true });', html=False)
		self.assertContains(response, '}, 100);', html=False)
		self.assertNotContains(response, 'codigoInput.blur()', html=False)
		self.assertNotContains(response, 'autofocus')
		self.assertContains(response, 'inputmode="text"', html=False)
		self.assertContains(response, 'let scannerBuffer =', html=False)
		self.assertContains(response, 'Scanner pronto', html=False)
		self.assertNotContains(response, 'Separado / Total', html=False)
		self.assertNotContains(response, 'item-atual-quantidade', html=False)
		self.assertContains(response, '123039 - (3/5)', html=False)
		self.assertNotContains(response, '>Finalizar<', html=False)
		self.assertNotContains(response, '<h1>Separação</h1>', html=False)

	def test_tela_separacao_nao_permite_fechamento_com_restricao_no_fluxo_operacional(self):
		self.tarefa.status = Tarefa.Status.EM_EXECUCAO
		self.tarefa.usuario = self.usuario
		self.tarefa.usuario_em_execucao = self.usuario
		self.tarefa.save(update_fields=['status', 'usuario', 'usuario_em_execucao', 'updated_at'])

		response = self.client.post(
			f'/separacao/{self.tarefa.id}/',
			{'acao': 'finalizar', 'status_final': Tarefa.Status.FECHADO_COM_RESTRICAO, 'motivo_restricao': 'FALTA ITEM'},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		self.tarefa.refresh_from_db()
		self.assertEqual(self.tarefa.status, Tarefa.Status.EM_EXECUCAO)
		self.assertNotContains(response, '>Finalizar<', html=False)

	def test_tela_separacao_aberta_exibe_aceite_sem_auto_iniciar(self):
		response = self.client.get(f'/separacao/{self.tarefa.id}/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Aceitar separação', html=False)
		self.tarefa.refresh_from_db()
		self.assertEqual(self.tarefa.status, Tarefa.Status.ABERTO)

	def test_tela_separacao_exibe_itens_nao_encontrados_da_tarefa(self):
		produto_ne = Produto.objects.create(
			cod_prod='NE999',
			descricao='Produto nao encontrado',
			cod_ean='789999999',
			categoria=Produto.Categoria.NAO_ENCONTRADO,
		)
		tarefa_ne = Tarefa.objects.create(
			nf=None,
			tipo=Tarefa.Tipo.ROTA,
			setor=Setor.Codigo.NAO_ENCONTRADO,
			rota=self.rota,
			status=Tarefa.Status.ABERTO,
		)
		TarefaItem.objects.create(
			tarefa=tarefa_ne,
			nf=self.nf,
			produto=produto_ne,
			quantidade_total='3.00',
			quantidade_separada='1.00',
		)

		response = self.client.get(f'/separacao/{tarefa_ne.id}/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'NE999 - (1/3)')
		self.assertContains(response, 'Produto nao encontrado')
		self.assertContains(response, '0 / 1')

	def test_lista_separacao_exibe_rota_no_card_mobile(self):
		self.client.login(username='gestor_setor', password='123456')
		response = self.client.get('/separacao/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'col-rota-mobile', html=False)
		self.assertContains(response, 'L01')

	def test_tela_conferencia_contém_script_de_polling(self):
		TarefaItem.objects.filter(tarefa=self.tarefa).update(quantidade_separada=F('quantidade_total'))
		self.tarefa.status = Tarefa.Status.CONCLUIDO
		self.tarefa.save(update_fields=['status', 'updated_at'])

		response = self.client.get(f'/conferencia/{self.nf.id}/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'const nfId =')
		self.assertContains(response, '/api/status/nf/${nfId}/')
		self.assertContains(response, '/api/conferencia/bipar/')
		self.assertContains(response, 'pollingIntervalMs = 10000', html=False)
		self.assertContains(response, 'visibilitychange', html=False)
		self.assertContains(response, 'pagehide', html=False)
		self.assertContains(response, 'clearInterval', html=False)
		self.assertContains(response, 'scheduleInputFocus', html=False)
		self.assertContains(response, 'codigoInput.focus({ preventScroll: true });', html=False)
		self.assertContains(response, '}, 100);', html=False)
		self.assertNotContains(response, 'codigoInput.blur()', html=False)
		self.assertNotContains(response, 'autofocus')
		self.assertContains(response, 'inputmode="text"', html=False)
		self.assertContains(response, 'let scannerBuffer =', html=False)
		self.assertContains(response, 'Scanner pronto', html=False)
		self.assertNotContains(response, 'Conferido / Total', html=False)
		self.assertNotContains(response, 'item-atual-quantidade', html=False)
		self.assertContains(response, '123039 - (3/5)', html=False)
		self.assertContains(response, 'conferencia-feedback', html=False)
		self.assertNotContains(response, '>Finalizar<', html=False)
		self.assertNotContains(response, '<h1>Conferência</h1>', html=False)


@override_settings(ROOT_URLCONF='config.urls')
class VisibilidadePorSetorTests(TestCase):
	def setUp(self):
		self.client = Client()
		self.rota = Rota.objects.create(nome='SET-01', cep_inicial='03000000', cep_final='03999999')
		self.cliente = Cliente.objects.create(nome='Cliente Setor', inscricao_estadual='123123123')
		self.produto = Produto.objects.create(
			cod_prod='SET001',
			descricao='Produto setor',
			cod_ean='789SET001',
			categoria=Produto.Categoria.LUBRIFICANTE,
		)
		self.nf = NotaFiscal.objects.create(
			chave_nfe='35111111111111111111550010000000011000000999',
			numero='1411638',
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.PENDENTE,
			data_emissao='2026-04-30T10:00:00-03:00',
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=False,
			ativa=True,
		)
		NotaFiscalItem.objects.create(nf=self.nf, produto=self.produto, quantidade='2.00')
		self.tarefa = Tarefa.objects.create(
			nf=None,
			tipo=Tarefa.Tipo.ROTA,
			setor=Setor.Codigo.LUBRIFICANTE,
			rota=self.rota,
			status=Tarefa.Status.EM_EXECUCAO,
		)
		TarefaItem.objects.create(
			tarefa=self.tarefa,
			nf=self.nf,
			produto=self.produto,
			quantidade_total='2.00',
			quantidade_separada='1.00',
		)
		self.gestor = Usuario.objects.create_user(
			username='gestor_setor',
			nome='Gestor Setor',
			perfil=Usuario.Perfil.GESTOR,
			setores=[Setor.Codigo.LUBRIFICANTE, Setor.Codigo.AGREGADO],
			password='123456',
			is_active=True,
		)
		self.operacional = Usuario.objects.create_user(
			username='separador_setor',
			nome='Separador Setor',
			perfil=Usuario.Perfil.SEPARADOR,
			setores=[Setor.Codigo.LUBRIFICANTE],
			password='123456',
			is_active=True,
		)
		self.sem_setor = Usuario.objects.create_user(
			username='sem_setor',
			nome='Sem Setor',
			perfil=Usuario.Perfil.SEPARADOR,
			setores=[],
			password='123456',
			is_active=True,
		)
		self.produto_agregado = Produto.objects.create(
			cod_prod='SET002',
			descricao='Produto agregado setor',
			cod_ean='789SET002',
			categoria=Produto.Categoria.AGREGADO,
		)
		self.tarefa_agregado = Tarefa.objects.create(
			nf=None,
			tipo=Tarefa.Tipo.ROTA,
			setor=Setor.Codigo.AGREGADO,
			rota=self.rota,
			status=Tarefa.Status.ABERTO,
		)
		TarefaItem.objects.create(
			tarefa=self.tarefa_agregado,
			nf=self.nf,
			produto=self.produto_agregado,
			quantidade_total='3.00',
			quantidade_separada='0.00',
		)

	def test_gestor_e_operacional_com_mesmo_setor_veem_mesma_tarefa(self):
		self.client.login(username='gestor_setor', password='123456')
		resp_gestor = self.client.get('/separacao/')
		self.client.logout()
		self.client.login(username='separador_setor', password='123456')
		resp_operacional = self.client.get('/separacao/')

		self.assertEqual(resp_gestor.status_code, 200)
		self.assertEqual(resp_operacional.status_code, 200)
		self.assertContains(resp_gestor, '1411638')
		self.assertContains(resp_operacional, '1411638')

	def test_usuario_sem_setor_nao_visualiza_tarefas(self):
		self.client.login(username='sem_setor', password='123456')
		response = self.client.get('/separacao/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Nenhuma tarefa disponível')

	def test_usuario_multi_setor_visualiza_tarefas_dos_dois_setores(self):
		usuario_multi = Usuario.objects.create_user(
			username='multi_setor',
			nome='Multi Setor',
			perfil=Usuario.Perfil.SEPARADOR,
			setores=[Setor.Codigo.LUBRIFICANTE, Setor.Codigo.AGREGADO],
			password='123456',
			is_active=True,
		)
		self.client.login(username='multi_setor', password='123456')
		response = self.client.get('/separacao/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, str(self.tarefa.id))
		self.assertContains(response, str(self.tarefa_agregado.id))

	def test_dashboard_resumo_separacao_consistente_com_listagem(self):
		self.client.login(username='gestor_setor', password='123456')
		resp_lista = self.client.get('/separacao/')
		resp_resumo = self.client.get('/api/dashboard/resumo/')

		self.assertEqual(resp_lista.status_code, 200)
		self.assertEqual(resp_resumo.status_code, 200)
		payload = resp_resumo.json()
		# Volume total = soma das quantidades das linhas do dashboard (2 + 3)
		self.assertEqual(payload['total'], 5.0)
		self.assertEqual(payload['pendente'], payload['total'] - payload['separado'])
		self.assertContains(resp_lista, str(self.tarefa.id))
		self.assertContains(resp_lista, str(self.tarefa_agregado.id))

	def test_finalizar_tarefa_remove_da_fila_e_atualiza_resumo(self):
		self.tarefa.status = Tarefa.Status.CONCLUIDO
		self.tarefa.save(update_fields=['status', 'updated_at'])
		self.client.login(username='gestor_setor', password='123456')
		resp_lista = self.client.get('/separacao/')
		resp_resumo = self.client.get('/api/dashboard/resumo/')

		self.assertEqual(resp_lista.status_code, 200)
		self.assertEqual(resp_resumo.status_code, 200)
		payload = resp_resumo.json()
		# Tarefa concluída some da fila; permanece apenas o volume da tarefa agregada (3), ainda pendente
		self.assertEqual(payload['total'], 3.0)
		self.assertEqual(payload['separado'], 0.0)
		self.assertEqual(payload['pendente'], 3.0)
		self.assertNotContains(resp_lista, f'/separacao/{self.tarefa.id}/')


@override_settings(ROOT_URLCONF='config.urls')
class SeparacaoAgrupamentoTests(TestCase):
	def setUp(self):
		self.client = Client()
		self.usuario_agregado = Usuario.objects.create_user(
			username='gestor_agregado',
			nome='Gestor Agregado',
			perfil=Usuario.Perfil.GESTOR,
			setores=[Setor.Codigo.AGREGADO],
			password='123456',
			is_active=True,
		)
		self.usuario_filtros = Usuario.objects.create_user(
			username='gestor_filtros',
			nome='Gestor Filtros',
			perfil=Usuario.Perfil.GESTOR,
			setores=[Setor.Codigo.FILTROS],
			password='123456',
			is_active=True,
		)

		self.rota = Rota.objects.create(nome='AGR-01', cep_inicial='02000000', cep_final='02999999')
		self.cliente = Cliente.objects.create(nome='Cliente Agrupamento', inscricao_estadual='999888777')
		self.produto_agregado = Produto.objects.create(cod_prod='AGR001', descricao='Produto agregado', cod_ean='789AGR001', categoria=Produto.Categoria.AGREGADO)
		self.produto_filtro_dup = Produto.objects.create(cod_prod='FLT777', descricao='Filtro duplicado', cod_ean='789FLT777', categoria=Produto.Categoria.FILTROS)

		self.nf_1 = NotaFiscal.objects.create(
			chave_nfe='35111111111111111111550010000000011000000666',
			numero='1410290',
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.NORMAL,
			data_emissao='2026-04-24T11:00:00-03:00',
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=False,
			ativa=True,
		)
		self.nf_2 = NotaFiscal.objects.create(
			chave_nfe='35111111111111111111550010000000011000000777',
			numero='1410291',
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.NORMAL,
			data_emissao='2026-04-24T12:00:00-03:00',
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=False,
			ativa=True,
		)

		self.tarefa_agregado = Tarefa.objects.create(
			nf=None,
			tipo=Tarefa.Tipo.ROTA,
			setor=Setor.Codigo.AGREGADO,
			rota=self.rota,
			status=Tarefa.Status.ABERTO,
		)
		TarefaItem.objects.create(tarefa=self.tarefa_agregado, nf=self.nf_1, produto=self.produto_agregado, quantidade_total='10.00', quantidade_separada='4.00')
		TarefaItem.objects.create(tarefa=self.tarefa_agregado, nf=self.nf_2, produto=self.produto_agregado, quantidade_total='7.00', quantidade_separada='1.00')

		self.tarefa_filtros_nf = Tarefa.objects.create(
			nf=None,
			tipo=Tarefa.Tipo.ROTA,
			setor=Setor.Codigo.FILTROS,
			rota=self.rota,
			status=Tarefa.Status.ABERTO,
		)
		TarefaItem.objects.create(tarefa=self.tarefa_filtros_nf, nf=self.nf_1, produto=self.produto_filtro_dup, quantidade_total='2.00', quantidade_separada='1.00')
		TarefaItem.objects.create(tarefa=self.tarefa_filtros_nf, nf=self.nf_2, produto=self.produto_filtro_dup, quantidade_total='4.00', quantidade_separada='0.00')

	def test_status_tarefa_api_agrupa_agregado_por_produto_e_rota(self):
		self.client.login(username='gestor_agregado', password='123456')
		response = self.client.get(f'/api/status/tarefa/{self.tarefa_agregado.id}/')

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertEqual(len(payload['itens']), 1)
		self.assertEqual(payload['itens'][0]['produto'], 'AGR001')
		self.assertEqual(payload['itens'][0]['quantidade_total'], 17.0)
		self.assertEqual(payload['itens'][0]['quantidade_separada'], 5.0)
		self.assertTrue(payload['itens'][0]['agrupado'])

	def test_status_tarefa_api_mantem_filtros_separados_por_nf(self):
		self.client.login(username='gestor_filtros', password='123456')
		response = self.client.get(f'/api/status/tarefa/{self.tarefa_filtros_nf.id}/')

		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertEqual(len(payload['itens']), 2)
		self.assertEqual({item['nf_numero'] for item in payload['itens']}, {'1410290', '1410291'})
		self.assertTrue(all(not item['agrupado'] for item in payload['itens']))

	def test_tela_separacao_exibe_agregado_agrupado_em_linha_unica(self):
		self.client.login(username='gestor_agregado', password='123456')
		response = self.client.get(f'/separacao/{self.tarefa_agregado.id}/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'AGR001')
		self.assertContains(response, 'Produto agregado')
		self.assertContains(response, '5 / 17')


@override_settings(ROOT_URLCONF='config.urls')
class LiberacaoDivergenciaWebTests(TestCase):
	def setUp(self):
		self.client = Client()
		self.gestor = Usuario.objects.create_user(
			username='gestor_liberacao',
			nome='Gestor Liberacao',
			perfil=Usuario.Perfil.GESTOR,
			setores=[Setor.Codigo.FILTROS],
			password='123456',
			is_active=True,
		)
		self.conferente = Usuario.objects.create_user(
			username='conferente_liberacao',
			nome='Conferente Liberacao',
			perfil=Usuario.Perfil.CONFERENTE,
			setores=[Setor.Codigo.FILTROS, Setor.Codigo.NAO_ENCONTRADO],
			password='123456',
			is_active=True,
		)
		self.rota = Rota.objects.create(nome='L02', cep_inicial='02000000', cep_final='02999999')
		self.cliente = Cliente.objects.create(nome='Cliente Liberacao', inscricao_estadual='987654321')
		self.produto = Produto.objects.create(cod_prod='778899', descricao='Filtro cabine', cod_ean='789778899', categoria=Produto.Categoria.FILTROS)

		self.nf = NotaFiscal.objects.create(
			chave_nfe='35111111111111111111550010000000022000000555',
			numero='1410999',
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.BLOQUEADA_COM_RESTRICAO,
			data_emissao='2026-04-24T10:00:00-03:00',
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=True,
			ativa=True,
		)
		NotaFiscalItem.objects.create(nf=self.nf, produto=self.produto, quantidade='2.00')

		self.tarefa = Tarefa.objects.create(
			nf=self.nf,
			tipo=Tarefa.Tipo.FILTRO,
			setor=Setor.Codigo.FILTROS,
			rota=self.rota,
			status=Tarefa.Status.FECHADO_COM_RESTRICAO,
		)
		TarefaItem.objects.create(tarefa=self.tarefa, nf=self.nf, produto=self.produto, quantidade_total='2.00', quantidade_separada='1.00', possui_restricao=True)

	def test_gestor_libera_tarefa_com_senha_e_gera_auditoria(self):
		self.client.login(username='gestor_liberacao', password='123456')

		response = self.client.post(
			f'/liberacao/tarefa/{self.tarefa.id}/',
			{'senha': '123456', 'motivo': 'Aprovado pela gestao', 'next': '/dashboard/separacao/'},
		)

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response['Location'], '/dashboard/separacao/')
		self.tarefa.refresh_from_db()
		self.nf.refresh_from_db()
		self.assertEqual(self.tarefa.status, Tarefa.Status.LIBERADO_COM_RESTRICAO)
		self.assertEqual(self.nf.status, NotaFiscal.Status.LIBERADA_COM_RESTRICAO)
		auditoria = LiberacaoDivergencia.objects.get(tarefa=self.tarefa)
		self.assertEqual(auditoria.usuario, self.gestor)
		self.assertEqual(auditoria.status_anterior, Tarefa.Status.FECHADO_COM_RESTRICAO)
		self.assertEqual(auditoria.status_novo, Tarefa.Status.LIBERADO_COM_RESTRICAO)

	def test_liberacao_exige_senha_valida(self):
		self.client.login(username='gestor_liberacao', password='123456')

		response = self.client.post(
			f'/liberacao/tarefa/{self.tarefa.id}/',
			{'senha': 'senha-invalida', 'motivo': 'Tentativa sem senha valida'},
		)

		self.assertEqual(response.status_code, 302)
		self.tarefa.refresh_from_db()
		self.assertEqual(self.tarefa.status, Tarefa.Status.FECHADO_COM_RESTRICAO)


	def test_conferencia_pode_iniciar_apos_liberacao_da_tarefa(self):
		self.client.login(username='gestor_liberacao', password='123456')
		self.client.post(
			f'/liberacao/tarefa/{self.tarefa.id}/',
			{'senha': '123456', 'motivo': 'Liberacao operacional'},
		)

		self.client.logout()
		self.client.login(username='conferente_liberacao', password='123456')
		response = self.client.post('/api/conferencia/iniciar/', {'nf_id': self.nf.id}, content_type='application/json')

		self.assertIn(response.status_code, {200, 400})
		if response.status_code == 200:
			self.assertEqual(response.json()['status'], Conferencia.Status.EM_CONFERENCIA)

	def test_gestor_libera_nf_com_divergencia_e_relatorio_exibe_registro(self):
		Conferencia.objects.create(nf=self.nf, conferente=self.conferente, status=Conferencia.Status.DIVERGENCIA)
		self.client.login(username='gestor_liberacao', password='123456')

		response = self.client.post(
			f'/liberacao/nf/{self.nf.id}/',
			{'senha': '123456', 'motivo': 'Divergencia aceita pela gestao', 'next': '/relatorio/liberacoes/'},
		)

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response['Location'], '/relatorio/liberacoes/')
		self.nf.refresh_from_db()
		self.assertFalse(self.nf.bloqueada)
		self.assertEqual(self.nf.status, NotaFiscal.Status.LIBERADA_COM_RESTRICAO)
		ultima_conferencia = self.nf.conferencias.order_by('-created_at').first()
		self.assertEqual(ultima_conferencia.status, Conferencia.Status.LIBERADO_COM_RESTRICAO)

		relatorio = self.client.get('/relatorio/liberacoes/?nf=1410999')
		self.assertEqual(relatorio.status_code, 200)
		self.assertContains(relatorio, 'Divergencia aceita pela gestao')
		self.assertContains(relatorio, 'LIBERADO_COM_RESTRICAO')

	def test_usuario_nao_gestor_nao_pode_liberar_divergencia(self):
		self.client.login(username='conferente_liberacao', password='123456')

		response = self.client.post(
			f'/liberacao/tarefa/{self.tarefa.id}/',
			{'senha': '123456', 'motivo': 'Nao deveria passar'},
		)

		self.assertEqual(response.status_code, 302)
		self.tarefa.refresh_from_db()
		self.assertEqual(self.tarefa.status, Tarefa.Status.FECHADO_COM_RESTRICAO)

	def test_excluir_tarefa_finalizada_permanece_bloqueado_por_status(self):
		self.client.login(username='gestor_liberacao', password='123456')
		self.tarefa.status = Tarefa.Status.CONCLUIDO
		self.tarefa.save(update_fields=['status', 'updated_at'])

		response = self.client.post(
			f'/tarefas/excluir/{self.tarefa.id}/',
			{'motivo': 'Nao faz parte da separacao'},
			HTTP_X_REQUESTED_WITH='XMLHttpRequest',
		)

		self.assertEqual(response.status_code, 400)
		self.assertEqual(response.json()['erro'], 'Tarefa finalizada não pode ser excluída.')
		self.tarefa.refresh_from_db()
		self.assertTrue(self.tarefa.ativo)
		self.assertFalse(LiberacaoDivergencia.objects.filter(tarefa=self.tarefa, status_novo='EXCLUIDO').exists())

	def test_excluir_nf_finalizada_permanece_bloqueado_por_status(self):
		self.client.login(username='gestor_liberacao', password='123456')
		self.nf.status = NotaFiscal.Status.CONCLUIDO
		self.nf.bloqueada = False
		self.nf.save(update_fields=['status', 'bloqueada', 'updated_at'])
		Conferencia.objects.create(nf=self.nf, conferente=self.conferente, status=Conferencia.Status.OK)

		response = self.client.post(
			f'/conferencia/excluir/{self.nf.id}/',
			{'motivo': 'Registro encerrado indevido'},
			HTTP_X_REQUESTED_WITH='XMLHttpRequest',
		)

		self.assertEqual(response.status_code, 400)
		self.assertEqual(response.json()['erro'], 'Conferência finalizada não pode ser excluída.')
		self.nf.refresh_from_db()
		self.assertTrue(self.nf.ativa)
		self.assertFalse(LiberacaoDivergencia.objects.filter(nf=self.nf, tarefa__isnull=True, status_novo='EXCLUIDO').exists())


@override_settings(ROOT_URLCONF='config.urls')
class LimpezaImportacaoWebTests(TestCase):
	def setUp(self):
		self.client = Client()
		self.admin = Usuario.objects.create_user(
			username='admin_limpeza',
			nome='Admin Limpeza',
			perfil=Usuario.Perfil.GESTOR,
			setores=[Setor.Codigo.NAO_ENCONTRADO],
			password='123456',
			is_active=True,
			is_staff=True,
			is_superuser=True,
		)
		self.gestor = Usuario.objects.create_user(
			username='gestor_limpeza',
			nome='Gestor Limpeza',
			perfil=Usuario.Perfil.GESTOR,
			setores=[Setor.Codigo.NAO_ENCONTRADO],
			password='123456',
			is_active=True,
		)
		self.rota = Rota.objects.create(nome='LIMP-01', cep_inicial='01000000', cep_final='01999999')
		self.cliente = Cliente.objects.create(nome='Cliente Limpeza', inscricao_estadual='445566778')
		self.produto = Produto.objects.create(
			cod_prod='LIMP001',
			descricao='Produto Limpeza',
			cod_ean='789000111',
			categoria=Produto.Categoria.FILTROS,
		)

	def _criar_entrada(self, chave, numero, dias_atras):
		arquivo = SimpleUploadedFile(f'{numero}.xml', b'<xml/>', content_type='text/xml')
		entrada = EntradaNF.objects.create(chave_nf=chave, numero_nf=numero, xml=arquivo, status=EntradaNF.Status.PROCESSADO)
		EntradaNF.objects.filter(id=entrada.id).update(
			data_importacao=timezone.now() - timedelta(days=dias_atras),
			created_at=timezone.now() - timedelta(days=dias_atras),
		)
		entrada.refresh_from_db()
		return entrada

	def _criar_nf(self, chave, numero, dias_atras, ativa=False):
		nf = NotaFiscal.objects.create(
			chave_nfe=chave,
			numero=numero,
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.BLOQUEADA_COM_RESTRICAO,
			data_emissao=timezone.now() - timedelta(days=dias_atras),
			status_fiscal=NotaFiscal.StatusFiscal.CANCELADA,
			bloqueada=True,
			ativa=ativa,
		)
		NotaFiscalItem.objects.create(nf=nf, produto=self.produto, quantidade='1.00')
		return nf

	def test_liberar_entrada_com_xml_ausente_nao_quebra_e_registra_auditoria(self):
		self.client.login(username='admin_limpeza', password='123456')
		entrada = EntradaNF.objects.create(
			chave_nf='35111111111111111111550010000000010000000777',
			numero_nf='1777',
			xml='xmls/inexistente.xml',
			status=EntradaNF.Status.AGUARDANDO,
		)

		response = self.client.post(f'/importar/fila/{entrada.id}/liberar/', follow=True)

		self.assertEqual(response.status_code, 200)
		entrada.refresh_from_db()
		self.assertEqual(entrada.status, EntradaNF.Status.AGUARDANDO)
		self.assertTrue(Log.objects.filter(usuario=self.admin, acao='XML STORAGE INCONSISTENTE').exists())
		self.assertContains(response, 'XML indisponível para a entrada', html=False)

	def test_liberar_entrada_com_xml_ausente_mas_nf_existente_libera_pela_chave(self):
		self.client.login(username='admin_limpeza', password='123456')
		chave = '35111111111111111111550010000000010000000888'
		entrada = EntradaNF.objects.create(
			chave_nf=chave,
			numero_nf='1888',
			xml='xmls/inexistente-888.xml',
			status=EntradaNF.Status.PROCESSADO,
		)
		NotaFiscal.objects.create(
			chave_nfe=chave,
			numero='1888',
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.PENDENTE,
			data_emissao=timezone.now(),
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=False,
			ativa=True,
		)

		response = self.client.post(f'/importar/fila/{entrada.id}/liberar/', follow=True)

		self.assertEqual(response.status_code, 200)
		entrada.refresh_from_db()
		self.assertEqual(entrada.status, EntradaNF.Status.LIBERADO)
		self.assertTrue(Log.objects.filter(usuario=self.admin, acao='XML STORAGE INCONSISTENTE').exists())
		self.assertTrue(Log.objects.filter(usuario=self.admin, acao='LIBERACAO ENTRADA SEM XML').exists())
		self.assertContains(response, 'liberada sem o arquivo XML', html=False)

	def test_importacao_persiste_backup_do_xml_e_restabelece_arquivo_na_liberacao(self):
		self.client.login(username='admin_limpeza', password='123456')
		xml_path = Path(__file__).resolve().parents[2] / 'xmls' / 'xml_autorizado.xml'
		arquivo = SimpleUploadedFile(
			xml_path.name,
			xml_path.read_bytes(),
			content_type='text/xml',
		)

		response_importacao = self.client.post('/importar/', {'xml_files': [arquivo]}, follow=True)

		self.assertEqual(response_importacao.status_code, 200)
		entrada = EntradaNF.objects.get(chave_nf='35111111111111111111550010000000011000000010')
		self.assertTrue(entrada.xml_backup_gzip)
		self.assertEqual(entrada.status, EntradaNF.Status.AGUARDANDO)
		xml_name = entrada.xml.name
		entrada.xml.delete(save=False)
		self.assertFalse(entrada.xml.storage.exists(xml_name))

		response_liberacao = self.client.post(f'/importar/fila/{entrada.id}/liberar/', follow=True)

		self.assertEqual(response_liberacao.status_code, 200)
		entrada.refresh_from_db()
		self.assertEqual(entrada.status, EntradaNF.Status.LIBERADO)
		self.assertTrue(entrada.xml.storage.exists(entrada.xml.name))
		self.assertTrue(NotaFiscal.objects.filter(chave_nfe=entrada.chave_nf).exists())
		self.assertTrue(
			Log.objects.filter(usuario=self.admin, acao='XML STORAGE INCONSISTENTE', detalhe__icontains='backup persistente').exists()
		)

	def test_tela_importacao_xml_exibe_limite_de_700_arquivos(self):
		self.client.login(username='admin_limpeza', password='123456')

		response = self.client.get('/importar/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, f'Máximo de {MAX_XML_FILES_POR_ENVIO} arquivos por envio no navegador.', html=False)

	def test_importacao_xml_bloqueia_envio_acima_de_700_arquivos(self):
		self.client.login(username='admin_limpeza', password='123456')
		arquivos = [
			SimpleUploadedFile(f'xml-{indice}.xml', b'<xml/>', content_type='text/xml')
			for indice in range(MAX_XML_FILES_POR_ENVIO + 1)
		]

		response = self.client.post('/importar/', {'xml_files': arquivos}, follow=True)

		self.assertEqual(response.status_code, 200)
		self.assertContains(
			response,
			f'Limite máximo de {MAX_XML_FILES_POR_ENVIO} arquivos por envio. Divida o lote e tente novamente.',
			html=False,
		)
		self.assertEqual(EntradaNF.objects.count(), 0)

	def test_bloqueia_limpeza_sem_base_maior_que_60_dias(self):
		self.client.login(username='admin_limpeza', password='123456')
		self._criar_entrada('35111111111111111111550010000000010000000001', '1001', dias_atras=20)

		response = self.client.post('/importar/fila/limpeza/', {'confirmar_limpeza': 'SIM'})

		self.assertEqual(response.status_code, 302)
		self.assertEqual(EntradaNF.objects.count(), 1)


@override_settings(ROOT_URLCONF='config.urls')
class PaginacaoListasGerenciaisTests(TestCase):
	def setUp(self):
		self.client = Client()
		self.usuario = Usuario.objects.create_user(
			username='gestor_paginacao',
			nome='Gestor Paginacao',
			perfil=Usuario.Perfil.GESTOR,
			setores=[Setor.Codigo.NAO_ENCONTRADO],
			password='123456',
			is_active=True,
		)
		self.client.login(username='gestor_paginacao', password='123456')
		self.rota = Rota.objects.create(nome='PAG-01', cep_inicial='01000000', cep_final='01999999')
		self.cliente = Cliente.objects.create(nome='Cliente Paginacao', inscricao_estadual='99887766')
		self.produto = Produto.objects.create(
			cod_prod='PAG001',
			descricao='Produto Paginacao',
			cod_ean='789123999',
			categoria=Produto.Categoria.FILTROS,
		)
		self.nf = NotaFiscal.objects.create(
			chave_nfe='35111111111111111111550010000000011000000999',
			numero='909090',
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.BLOQUEADA_COM_RESTRICAO,
			data_emissao=timezone.now(),
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=True,
			ativa=True,
		)
		NotaFiscalItem.objects.create(nf=self.nf, produto=self.produto, quantidade='1.00')

	def test_fila_importacao_limita_em_20_por_pagina(self):
		for indice in range(25):
			arquivo = SimpleUploadedFile(f'nf_{indice}.xml', b'<xml/>', content_type='text/xml')
			EntradaNF.objects.create(
				chave_nf=f'3511111111111111111155001000000001100000{indice:04d}',
				numero_nf=f'{1000 + indice}',
				xml=arquivo,
				status=EntradaNF.Status.AGUARDANDO,
			)

		response_pagina_1 = self.client.get('/importar/fila/')
		response_pagina_2 = self.client.get('/importar/fila/?page=2')

		self.assertEqual(response_pagina_1.status_code, 200)
		self.assertTrue(response_pagina_1.context['is_paginated'])
		self.assertEqual(len(response_pagina_1.context['entradas']), 20)
		self.assertContains(response_pagina_1, 'Página 1 de 2')
		self.assertEqual(len(response_pagina_2.context['entradas']), 5)

	def test_relatorio_liberacoes_limita_em_20_e_preserva_filtro(self):
		for indice in range(25):
			LiberacaoDivergencia.objects.create(
				usuario=self.usuario,
				nf=self.nf,
				motivo=f'Motivo {indice}',
				nf_numero=self.nf.numero,
				status_anterior='BLOQUEADA_COM_RESTRICAO',
				status_novo='LIBERADO_COM_RESTRICAO',
			)

		response = self.client.get('/relatorio/liberacoes/?usuario=gestor&page=2')

		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.context['is_paginated'])
		self.assertEqual(len(response.context['linhas']), 5)
		self.assertEqual(response.context['pagination_query'], '&usuario=gestor')
		self.assertContains(response, '?page=1&amp;usuario=gestor', html=False)


@override_settings(ROOT_URLCONF='config.urls')
class LimpezaImportacaoWebContinuationTests(LimpezaImportacaoWebTests):
	def test_remove_apenas_faixa_de_30_dias_mais_antiga(self):
		self.client.login(username='admin_limpeza', password='123456')
		e1 = self._criar_entrada('35111111111111111111550010000000010000000011', '1011', dias_atras=120)
		e2 = self._criar_entrada('35111111111111111111550010000000010000000012', '1012', dias_atras=110)
		e3 = self._criar_entrada('35111111111111111111550010000000010000000013', '1013', dias_atras=95)
		e4 = self._criar_entrada('35111111111111111111550010000000010000000014', '1014', dias_atras=80)
		e5 = self._criar_entrada('35111111111111111111550010000000010000000015', '1015', dias_atras=50)
		self._criar_nf(e1.chave_nf, e1.numero_nf, dias_atras=120)
		self._criar_nf(e2.chave_nf, e2.numero_nf, dias_atras=110)

		response = self.client.post('/importar/fila/limpeza/', {'confirmar_limpeza': 'SIM'})

		self.assertEqual(response.status_code, 302)
		self.assertFalse(EntradaNF.objects.filter(id__in=[e1.id, e2.id]).exists())
		self.assertEqual(EntradaNF.objects.filter(id__in=[e3.id, e4.id, e5.id]).count(), 2)
		self.assertFalse(NotaFiscal.objects.filter(chave_nfe__in=[e1.chave_nf, e2.chave_nf]).exists())

	def test_bloqueia_limpeza_quando_existir_vinculo_ativo(self):
		self.client.login(username='admin_limpeza', password='123456')
		entrada = self._criar_entrada('35111111111111111111550010000000010000000999', '1999', dias_atras=130)
		nf = self._criar_nf(entrada.chave_nf, entrada.numero_nf, dias_atras=130)
		Tarefa.objects.create(
			nf=nf,
			tipo=Tarefa.Tipo.FILTRO,
			setor=Setor.Codigo.FILTROS,
			rota=self.rota,
			status=Tarefa.Status.EM_EXECUCAO,
			ativo=True,
		)

		response = self.client.post('/importar/fila/limpeza/', {'confirmar_limpeza': 'SIM'})

		self.assertEqual(response.status_code, 302)
		self.assertTrue(EntradaNF.objects.filter(id=entrada.id).exists())
		self.assertTrue(NotaFiscal.objects.filter(id=nf.id).exists())

	def test_limpeza_so_disponivel_para_superuser(self):
		self.client.login(username='gestor_limpeza', password='123456')
		self._criar_entrada('35111111111111111111550010000000010000000888', '1888', dias_atras=130)

		response = self.client.post('/importar/fila/limpeza/', {'confirmar_limpeza': 'SIM'})

		self.assertEqual(response.status_code, 302)
		self.assertEqual(EntradaNF.objects.count(), 1)
