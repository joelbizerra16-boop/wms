from django.test import SimpleTestCase


class HealthCheckTests(SimpleTestCase):
	def test_healthcheck_returns_success(self):
		response = self.client.get('/api/health/')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json(), {'status': 'ok'})


from datetime import timedelta

from django.test import Client, TestCase, override_settings

from apps.clientes.models import Cliente
from apps.conferencia.models import Conferencia, ConferenciaItem
from apps.logs.models import LiberacaoDivergencia
from apps.nf.models import NotaFiscal, NotaFiscalItem
from apps.produtos.models import Produto
from apps.rotas.models import Rota
from apps.tarefas.models import Tarefa, TarefaItem
from apps.usuarios.models import Setor, Usuario


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

	def test_dashboard_conferencia_exibe_nf_e_resumo(self):
		response = self.client.get('/dashboard/conferencia/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Dashboard de Conferência')

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

	def test_dashboard_separacao_exibe_nf_de_item_consolidado(self):
		response = self.client.get('/dashboard/separacao/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '1410289')
		self.assertContains(response, 'Rodrigo')

	def test_detalhe_nf_exibe_pendencia_de_separacao(self):
		response = self.client.get(f'/conferencia/detalhe/{self.nf.id}/')

		self.assertEqual(response.status_code, 200)

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

	def test_tela_separacao_contém_script_de_polling(self):
		response = self.client.get(f'/separacao/{self.tarefa.id}/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '/api/tarefa-status/')
		self.assertContains(response, '/api/separacao/bipar/')
		self.assertContains(response, 'setInterval')

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
		self.assertContains(response, 'NE999')
		self.assertContains(response, 'Produto nao encontrado')
		self.assertContains(response, '1 / 3')

	def test_tela_conferencia_contém_script_de_polling(self):
		response = self.client.get(f'/conferencia/{self.nf.id}/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'const nfId =')
		self.assertContains(response, '/api/status/nf/${nfId}/')
		self.assertContains(response, '/api/conferencia/bipar/')
		self.assertContains(response, 'setInterval')


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
		self.assertEqual(payload['total'], 2)
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
		self.assertEqual(payload['total'], 1)
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
		self.assertFalse(LiberacaoDivergencia.objects.filter(tarefa=self.tarefa).exists())

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
