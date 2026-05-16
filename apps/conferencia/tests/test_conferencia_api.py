from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.conferencia.models import Conferencia, ConferenciaItem
from apps.nf.models import NotaFiscal, NotaFiscalItem
from apps.produtos.models import Produto
from apps.rotas.models import Rota
from apps.tarefas.models import Tarefa, TarefaItem
from apps.usuarios.models import Setor, Usuario


@override_settings(ROOT_URLCONF='config.urls')
class ConferenciaAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.usuario = Usuario.objects.create_user(
            username='conferente',
            nome='Conferente 1',
            perfil=Usuario.Perfil.CONFERENTE,
            setores=[Setor.Codigo.FILTROS],
            password='123456',
            is_active=True,
        )
        self.outro_usuario = Usuario.objects.create_user(
            username='conferente2',
            nome='Conferente 2',
            perfil=Usuario.Perfil.CONFERENTE,
            setores=[Setor.Codigo.AGREGADO],
            password='123456',
            is_active=True,
        )
        self.usuario_multi = Usuario.objects.create_user(
            username='conferente_multi',
            nome='Conferente Multi',
            perfil=Usuario.Perfil.CONFERENTE,
            setores=[Setor.Codigo.FILTROS, Setor.Codigo.AGREGADO],
            password='123456',
            is_active=True,
        )
        self.usuario_sem_setor = Usuario.objects.create_user(
            username='conferente_sem_setor',
            nome='Conferente Sem Setor',
            perfil=Usuario.Perfil.CONFERENTE,
            setores=[],
            password='123456',
            is_active=True,
        )
        self.client.force_authenticate(self.usuario)

        self.rota = Rota.objects.create(nome='Rota 1', cep_inicial='01000000', cep_final='01999999')
        self.cliente = Cliente.objects.create(nome='Cliente A', inscricao_estadual='1234567890')
        self.produto_1 = Produto.objects.create(
            cod_prod='PRD001',
            descricao='Produto 1',
            cod_ean='7890001',
            categoria=Produto.Categoria.AGREGADO,
        )
        self.produto_2 = Produto.objects.create(
            cod_prod='PRD002',
            descricao='Produto 2',
            cod_ean='7890002',
            categoria=Produto.Categoria.FILTROS,
        )
        self.nf = NotaFiscal.objects.create(
            chave_nfe='35111111111111111111550010000000011000000011',
            numero='456',
            cliente=self.cliente,
            rota=self.rota,
            status=NotaFiscal.Status.NORMAL,
            data_emissao='2026-04-23T10:00:00-03:00',
            status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
            bloqueada=False,
            ativa=True,
        )
        NotaFiscalItem.objects.create(nf=self.nf, produto=self.produto_1, quantidade='2.00')
        NotaFiscalItem.objects.create(nf=self.nf, produto=self.produto_2, quantidade='1.00')

        self.tarefa_1 = Tarefa.objects.create(
            nf=None,
            tipo=Tarefa.Tipo.ROTA,
            setor=Setor.Codigo.AGREGADO,
            rota=self.rota,
            status=Tarefa.Status.CONCLUIDO,
        )
        TarefaItem.objects.create(tarefa=self.tarefa_1, nf=self.nf, produto=self.produto_1, quantidade_total='2.00', quantidade_separada='2.00')
        self.tarefa_2 = Tarefa.objects.create(
            nf=self.nf,
            tipo=Tarefa.Tipo.FILTRO,
            setor=Setor.Codigo.FILTROS,
            rota=self.rota,
            status=Tarefa.Status.CONCLUIDO,
        )
        TarefaItem.objects.create(tarefa=self.tarefa_2, nf=self.nf, produto=self.produto_2, quantidade_total='1.00', quantidade_separada='1.00')

    def test_lista_nfs_disponiveis_e_inicia_conferencia(self):
        response_nfs = self.client.get('/api/conferencia/nfs/')

        self.assertEqual(response_nfs.status_code, 200)
        self.assertEqual(len(response_nfs.data), 1)
        self.assertEqual(response_nfs.data[0]['numero'], self.nf.numero)

        response_inicio = self.client.post('/api/conferencia/iniciar/', {'nf_id': self.nf.id}, format='json')

        self.assertEqual(response_inicio.status_code, 200)
        self.assertEqual(response_inicio.data['status'], Conferencia.Status.EM_CONFERENCIA)
        self.assertEqual(response_inicio.data['progresso']['esperado'], 1.0)
        self.assertEqual(ConferenciaItem.objects.filter(conferencia_id=response_inicio.data['id']).count(), 1)

    def test_bipagem_e_finalizacao_ok(self):
        conferencia_id = self.client.post('/api/conferencia/iniciar/', {'nf_id': self.nf.id}, format='json').data['id']

        self.client.post('/api/conferencia/bipar/', {'conferencia_id': conferencia_id, 'codigo': 'PRD001'}, format='json')
        self.client.post('/api/conferencia/bipar/', {'conferencia_id': conferencia_id, 'codigo': '7890001'}, format='json')
        response_final = self.client.post('/api/conferencia/bipar/', {'conferencia_id': conferencia_id, 'codigo': 'PRD002'}, format='json')

        self.assertEqual(response_final.status_code, 200)
        self.assertEqual(response_final.data['status'], 'ok')
        self.assertTrue(response_final.data['finalizado'])
        self.assertEqual(response_final.data['conferencia']['status'], Conferencia.Status.OK)
        self.assertEqual(response_final.data['conferencia']['progresso']['percentual'], 100.0)
        self.nf.refresh_from_db()
        self.assertFalse(self.nf.bloqueada)

    def test_divergencia_gera_retorno_para_separacao(self):
        conferencia_id = self.client.post('/api/conferencia/iniciar/', {'nf_id': self.nf.id}, format='json').data['id']
        item_1 = ConferenciaItem.objects.get(conferencia_id=conferencia_id, produto=self.produto_2)

        response_div = self.client.post(
            '/api/conferencia/divergencia/',
            {'item_id': item_1.id, 'motivo': ConferenciaItem.MotivoDivergencia.FALTA, 'observacao': 'faltou 1 unidade'},
            format='json',
        )
        response_final = self.client.post('/api/conferencia/finalizar/', {'conferencia_id': conferencia_id}, format='json')

        self.assertEqual(response_div.status_code, 200)
        self.assertEqual(response_final.status_code, 200)
        self.assertEqual(response_final.data['status'], Conferencia.Status.DIVERGENCIA)
        self.nf.refresh_from_db()
        self.assertTrue(self.nf.bloqueada)
        self.assertEqual(self.nf.status, NotaFiscal.Status.BLOQUEADA_COM_RESTRICAO)
        self.assertTrue(
            Tarefa.objects.filter(
                nf=self.nf,
                rota=self.nf.rota,
                status=Tarefa.Status.ABERTO,
                setor=Setor.Codigo.FILTROS,
            ).exists()
        )

    def test_nao_permite_iniciar_com_outro_conferente_ativo_no_mesmo_setor(self):
        usuario_mesmo_setor = Usuario.objects.create_user(
            username='conferente_filtros_2',
            nome='Conferente Filtros 2',
            perfil=Usuario.Perfil.CONFERENTE,
            setores=[Setor.Codigo.FILTROS],
            password='123456',
            is_active=True,
        )
        self.client.post('/api/conferencia/iniciar/', {'nf_id': self.nf.id}, format='json')
        self.client.force_authenticate(usuario_mesmo_setor)

        response = self.client.post('/api/conferencia/iniciar/', {'nf_id': self.nf.id}, format='json')

        self.assertEqual(response.status_code, 400)

    def test_nf_cancelada_nao_aparece_na_conferencia(self):
        self.nf.status_fiscal = NotaFiscal.StatusFiscal.CANCELADA
        self.nf.bloqueada = True
        self.nf.ativa = False
        self.nf.save(update_fields=['status_fiscal', 'bloqueada', 'ativa', 'updated_at'])

        response = self.client.get('/api/conferencia/nfs/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_conferente_multi_setor_visualiza_nf(self):
        self.client.force_authenticate(self.usuario_multi)
        response = self.client.get('/api/conferencia/nfs/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], self.nf.id)

    def test_inicia_conferencia_filtrando_itens_pelos_setores_do_conferente(self):
        response_filtros = self.client.post('/api/conferencia/iniciar/', {'nf_id': self.nf.id}, format='json')

        self.assertEqual(response_filtros.status_code, 200)
        conferencia_filtros = Conferencia.objects.get(id=response_filtros.data['id'])
        itens_filtros = list(conferencia_filtros.itens.select_related('produto').order_by('produto__cod_prod'))
        self.assertEqual([item.produto.cod_prod for item in itens_filtros], ['PRD002'])
        self.assertEqual(response_filtros.data['progresso']['esperado'], 1.0)

        self.client.force_authenticate(self.outro_usuario)
        response_agregado = self.client.post('/api/conferencia/iniciar/', {'nf_id': self.nf.id}, format='json')

        self.assertEqual(response_agregado.status_code, 200)
        conferencia_agregado = Conferencia.objects.get(id=response_agregado.data['id'])
        itens_agregado = list(conferencia_agregado.itens.select_related('produto').order_by('produto__cod_prod'))
        self.assertEqual([item.produto.cod_prod for item in itens_agregado], ['PRD001'])
        self.assertEqual(response_agregado.data['progresso']['esperado'], 2.0)

    def test_conferencia_respeita_setor_do_produto_quando_categoria_diverge(self):
        self.produto_1.setor = Setor.Codigo.AGREGADO
        self.produto_1.categoria = Produto.Categoria.FILTROS
        self.produto_1.save(update_fields=['setor', 'categoria', 'updated_at'])
        self.produto_2.setor = Setor.Codigo.FILTROS
        self.produto_2.categoria = Produto.Categoria.AGREGADO
        self.produto_2.save(update_fields=['setor', 'categoria', 'updated_at'])

        response_filtros = self.client.post('/api/conferencia/iniciar/', {'nf_id': self.nf.id}, format='json')

        self.assertEqual(response_filtros.status_code, 200)
        conferencia_filtros = Conferencia.objects.get(id=response_filtros.data['id'])
        itens_filtros = list(conferencia_filtros.itens.select_related('produto').order_by('produto__cod_prod'))
        self.assertEqual([item.produto.cod_prod for item in itens_filtros], ['PRD002'])

        self.client.force_authenticate(self.outro_usuario)
        response_agregado = self.client.post('/api/conferencia/iniciar/', {'nf_id': self.nf.id}, format='json')

        self.assertEqual(response_agregado.status_code, 200)
        conferencia_agregado = Conferencia.objects.get(id=response_agregado.data['id'])
        itens_agregado = list(conferencia_agregado.itens.select_related('produto').order_by('produto__cod_prod'))
        self.assertEqual([item.produto.cod_prod for item in itens_agregado], ['PRD001'])

    def test_fila_conferencia_atualiza_imediatamente_apos_finalizar_filtros(self):
        cliente = Cliente.objects.create(nome='Cliente Cache', inscricao_estadual='99887766')
        rota = Rota.objects.create(nome='Rota Cache', cep_inicial='02000000', cep_final='02999999')
        produto = Produto.objects.create(
            cod_prod='FLT999',
            descricao='Filtro Cache',
            cod_ean='7899999',
            categoria=Produto.Categoria.FILTROS,
        )
        nf = NotaFiscal.objects.create(
            chave_nfe='35111111111111111111550010000000011000000999',
            numero='9999',
            cliente=cliente,
            rota=rota,
            status=NotaFiscal.Status.NORMAL,
            data_emissao='2026-04-24T10:00:00-03:00',
            status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
            bloqueada=False,
            ativa=True,
        )
        NotaFiscalItem.objects.create(nf=nf, produto=produto, quantidade='1.00')
        separador = Usuario.objects.create_user(
            username='separador_cache',
            nome='Separador Cache',
            perfil=Usuario.Perfil.SEPARADOR,
            setores=[Setor.Codigo.FILTROS],
            password='123456',
            is_active=True,
        )
        tarefa = Tarefa.objects.create(
            nf=nf,
            tipo=Tarefa.Tipo.FILTRO,
            setor=Setor.Codigo.FILTROS,
            rota=rota,
            status=Tarefa.Status.ABERTO,
        )
        TarefaItem.objects.create(
            tarefa=tarefa,
            nf=nf,
            produto=produto,
            quantidade_total='1.00',
            quantidade_separada='0.00',
        )

        resposta_vazia = self.client.get('/api/conferencia/nfs/')
        self.assertEqual(resposta_vazia.status_code, 200)
        self.assertFalse(any(item['id'] == nf.id for item in resposta_vazia.data))

        self.client.force_authenticate(separador)
        self.client.post('/api/separacao/iniciar/', {'tarefa_id': tarefa.id}, format='json')
        self.client.post('/api/separacao/bipar/', {'tarefa_id': tarefa.id, 'codigo': produto.cod_prod}, format='json')
        response_finalizar = self.client.post(
            '/api/separacao/finalizar/',
            {'tarefa_id': tarefa.id, 'status': Tarefa.Status.CONCLUIDO},
            format='json',
        )
        self.assertEqual(response_finalizar.status_code, 200)

        self.client.force_authenticate(self.usuario)
        resposta_atualizada = self.client.get('/api/conferencia/nfs/')

        self.assertEqual(resposta_atualizada.status_code, 200)
        self.assertTrue(any(item['id'] == nf.id for item in resposta_atualizada.data))

    def test_conferente_sem_setor_nao_visualiza_nf_e_nao_inicia(self):
        self.client.force_authenticate(self.usuario_sem_setor)
        response_lista = self.client.get('/api/conferencia/nfs/')
        self.assertEqual(response_lista.status_code, 200)
        self.assertEqual(response_lista.data, [])

        response_inicio = self.client.post('/api/conferencia/iniciar/', {'nf_id': self.nf.id}, format='json')
        self.assertEqual(response_inicio.status_code, 400)
        self.assertEqual(response_inicio.data, {'erro': 'Usuário sem setor vinculado. Contate o administrador.'})

    def test_nf_cancelada_bloqueia_inicio_conferencia(self):
        self.nf.status_fiscal = NotaFiscal.StatusFiscal.CANCELADA
        self.nf.bloqueada = True
        self.nf.ativa = False
        self.nf.save(update_fields=['status_fiscal', 'bloqueada', 'ativa', 'updated_at'])

        response = self.client.post('/api/conferencia/iniciar/', {'nf_id': self.nf.id}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'erro': 'NF cancelada não pode ser processada'})

    def test_conferencia_liberada_permita_finalizar_com_pendencia(self):
        conferencia = Conferencia.objects.create(
            nf=self.nf,
            conferente=self.usuario,
            status=Conferencia.Status.LIBERADO_COM_RESTRICAO,
        )
        ConferenciaItem.objects.create(
            conferencia=conferencia,
            produto=self.produto_1,
            qtd_esperada='2.00',
            qtd_conferida='1.00',
            status=ConferenciaItem.Status.AGUARDANDO,
        )
        ConferenciaItem.objects.create(
            conferencia=conferencia,
            produto=self.produto_2,
            qtd_esperada='1.00',
            qtd_conferida='1.00',
            status=ConferenciaItem.Status.OK,
        )
        self.nf.status = NotaFiscal.Status.LIBERADA_COM_RESTRICAO
        self.nf.bloqueada = False
        self.nf.save(update_fields=['status', 'bloqueada', 'updated_at'])

        response = self.client.post('/api/conferencia/finalizar/', {'conferencia_id': conferencia.id}, format='json')

        self.assertEqual(response.status_code, 200)
        conferencia.refresh_from_db()
        self.assertEqual(conferencia.status, Conferencia.Status.CONCLUIDO_COM_RESTRICAO)