from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.nf.models import NotaFiscal
from apps.produtos.models import Produto
from apps.rotas.models import Rota
from apps.tarefas.models import Tarefa, TarefaItem
from apps.usuarios.models import Setor, Usuario


@override_settings(ROOT_URLCONF='config.urls')
class SeparacaoRealAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.rota = Rota.objects.create(nome='Rota 01', cep_inicial='00000000', cep_final='99999999')
        self.cliente = Cliente.objects.create(nome='Cliente A', inscricao_estadual='123456')

        self.usuario_lub = Usuario.objects.create_user(
            username='lub',
            nome='Lub',
            perfil=Usuario.Perfil.SEPARADOR,
            setores=[Setor.Codigo.LUBRIFICANTE],
            password='123456',
            is_active=True,
        )
        self.usuario_filtro = Usuario.objects.create_user(
            username='filtro',
            nome='Filtro',
            perfil=Usuario.Perfil.SEPARADOR,
            setores=[Setor.Codigo.FILTROS],
            password='123456',
            is_active=True,
        )
        self.usuario_lub_2 = Usuario.objects.create_user(
            username='lub2',
            nome='Lub 2',
            perfil=Usuario.Perfil.SEPARADOR,
            setores=[Setor.Codigo.LUBRIFICANTE],
            password='123456',
            is_active=True,
        )
        self.usuario_ne = Usuario.objects.create_user(
            username='naoencontrado',
            nome='Nao Encontrado',
            perfil=Usuario.Perfil.SEPARADOR,
            setores=[Setor.Codigo.NAO_ENCONTRADO],
            password='123456',
            is_active=True,
        )
        self.usuario_sem_setor = Usuario.objects.create_user(
            username='semsetor',
            nome='Sem Setor',
            perfil=Usuario.Perfil.SEPARADOR,
            setores=[],
            password='123456',
            is_active=True,
        )
        self.usuario_gestor = Usuario.objects.create_user(
            username='gestor',
            nome='Gestor',
            perfil=Usuario.Perfil.GESTOR,
            setores=[Setor.Codigo.NAO_ENCONTRADO],
            password='123456',
            is_active=True,
        )
        self.usuario_multi = Usuario.objects.create_user(
            username='multi',
            nome='Multi Setor',
            perfil=Usuario.Perfil.SEPARADOR,
            setores=[Setor.Codigo.LUBRIFICANTE, Setor.Codigo.FILTROS],
            password='123456',
            is_active=True,
        )

        self.produto_lub = Produto.objects.create(
            cod_prod='LUB001',
            descricao='Lubrificante',
            cod_ean='7891001',
            categoria=Produto.Categoria.LUBRIFICANTE,
        )
        self.produto_filtro = Produto.objects.create(
            cod_prod='FLT001',
            descricao='Filtro',
            cod_ean='7891002',
            categoria=Produto.Categoria.FILTROS,
        )
        self.produto_ne = Produto.objects.create(
            cod_prod='NE001',
            descricao='Sem classificacao',
            cod_ean='7891003',
            categoria=Produto.Categoria.NAO_ENCONTRADO,
        )

        self.nf = NotaFiscal.objects.create(
            chave_nfe='35111111111111111111550010000000011000000099',
            numero='999',
            cliente=self.cliente,
            rota=self.rota,
            data_emissao='2026-04-24T10:00:00-03:00',
            status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
            bloqueada=False,
            ativa=True,
        )

        self.tarefa_rota_lub = Tarefa.objects.create(
            nf=None,
            tipo=Tarefa.Tipo.ROTA,
            setor=Setor.Codigo.LUBRIFICANTE,
            rota=self.rota,
            status=Tarefa.Status.ABERTO,
        )
        TarefaItem.objects.create(
            tarefa=self.tarefa_rota_lub,
            nf=self.nf,
            produto=self.produto_lub,
            quantidade_total='2.00',
            quantidade_separada='0.00',
        )

        self.tarefa_filtro = Tarefa.objects.create(
            nf=self.nf,
            tipo=Tarefa.Tipo.FILTRO,
            setor=Setor.Codigo.FILTROS,
            rota=self.rota,
            status=Tarefa.Status.ABERTO,
        )
        TarefaItem.objects.create(
            tarefa=self.tarefa_filtro,
            nf=self.nf,
            produto=self.produto_filtro,
            quantidade_total='2.00',
            quantidade_separada='0.00',
        )

        self.tarefa_rota_ne = Tarefa.objects.create(
            nf=None,
            tipo=Tarefa.Tipo.ROTA,
            setor=Setor.Codigo.NAO_ENCONTRADO,
            rota=self.rota,
            status=Tarefa.Status.ABERTO,
        )
        TarefaItem.objects.create(
            tarefa=self.tarefa_rota_ne,
            nf=self.nf,
            produto=self.produto_ne,
            quantidade_total='1.00',
            quantidade_separada='0.00',
        )

    def _autenticar(self, usuario):
        self.client.force_authenticate(user=usuario)

    def test_lista_respeita_setor_operacional(self):
        self._autenticar(self.usuario_lub)
        response_lub = self.client.get('/api/separacao/tarefas/')
        self.assertEqual(response_lub.status_code, 200)
        self.assertEqual([item['id'] for item in response_lub.data], [self.tarefa_rota_lub.id])
        self.assertEqual(response_lub.data[0]['operacao'], 'ROTA')

        self._autenticar(self.usuario_filtro)
        response_filtro = self.client.get('/api/separacao/tarefas/')
        self.assertEqual(response_filtro.status_code, 200)
        self.assertEqual([item['id'] for item in response_filtro.data], [self.tarefa_filtro.id])
        self.assertEqual(response_filtro.data[0]['operacao'], 'NF')

        self._autenticar(self.usuario_ne)
        response_ne = self.client.get('/api/separacao/tarefas/')
        self.assertEqual(response_ne.status_code, 200)
        self.assertEqual([item['id'] for item in response_ne.data], [self.tarefa_rota_ne.id])

    def test_gestor_visualiza_tarefas_dos_setores_vinculados(self):
        self._autenticar(self.usuario_gestor)

        response = self.client.get('/api/separacao/tarefas/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {item['id'] for item in response.data},
            {self.tarefa_rota_ne.id},
        )

    def test_separador_sem_setor_nao_visualiza_tarefas(self):
        self._autenticar(self.usuario_sem_setor)

        response = self.client.get('/api/separacao/tarefas/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_separador_multi_setor_visualiza_tarefas_de_todos_setores_permitidos(self):
        self._autenticar(self.usuario_multi)
        response = self.client.get('/api/separacao/tarefas/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {item['id'] for item in response.data},
            {self.tarefa_rota_lub.id, self.tarefa_filtro.id},
        )

    def test_separador_sem_setor_nao_pode_iniciar_tarefa(self):
        self._autenticar(self.usuario_sem_setor)
        response = self.client.post('/api/separacao/iniciar/', {'tarefa_id': self.tarefa_rota_lub.id}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'erro': 'Usuário sem setor vinculado. Contate o administrador.'})

    def test_iniciar_tarefa_atribui_responsavel_e_marca_em_execucao(self):
        self._autenticar(self.usuario_lub)

        response = self.client.post('/api/separacao/iniciar/', {'tarefa_id': self.tarefa_rota_lub.id}, format='json')

        self.assertEqual(response.status_code, 200)
        self.tarefa_rota_lub.refresh_from_db()
        self.assertEqual(self.tarefa_rota_lub.status, Tarefa.Status.EM_EXECUCAO)
        self.assertEqual(self.tarefa_rota_lub.usuario_id, self.usuario_lub.id)

    def test_tarefa_em_execucao_nao_pode_ser_assumida_por_outro_separador(self):
        self.tarefa_rota_lub.status = Tarefa.Status.EM_EXECUCAO
        self.tarefa_rota_lub.usuario = self.usuario_lub
        self.tarefa_rota_lub.save(update_fields=['status', 'usuario', 'updated_at'])

        self._autenticar(self.usuario_lub_2)
        response = self.client.post('/api/separacao/iniciar/', {'tarefa_id': self.tarefa_rota_lub.id}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'erro': 'Tarefa ja esta em execucao por outro usuario'})

        self._autenticar(self.usuario_lub)
        response_mesmo_usuario = self.client.post('/api/separacao/iniciar/', {'tarefa_id': self.tarefa_rota_lub.id}, format='json')

        self.assertEqual(response_mesmo_usuario.status_code, 200)
        self.assertEqual(response_mesmo_usuario.data['status'], Tarefa.Status.EM_EXECUCAO)

    def test_bipagem_aceita_codigo_ou_ean_e_conclui_tarefa_de_rota(self):
        self._autenticar(self.usuario_lub)
        self.client.post('/api/separacao/iniciar/', {'tarefa_id': self.tarefa_rota_lub.id}, format='json')

        response_codigo = self.client.post(
            '/api/separacao/bipar/',
            {'tarefa_id': self.tarefa_rota_lub.id, 'codigo': self.produto_lub.cod_prod},
            format='json',
        )
        response_ean = self.client.post(
            '/api/separacao/bipar/',
            {'tarefa_id': self.tarefa_rota_lub.id, 'codigo': self.produto_lub.cod_ean},
            format='json',
        )

        self.assertEqual(response_codigo.status_code, 200)
        self.assertEqual(response_ean.status_code, 200)
        self.assertIn('Produto validado no setor', response_ean.data['feedback'])
        self.assertEqual(response_ean.data['cor'], 'verde')
        self.assertEqual(response_ean.data['som'], 'beep-curto')
        self.tarefa_rota_lub.refresh_from_db()
        self.assertEqual(self.tarefa_rota_lub.status, Tarefa.Status.CONCLUIDO)

    def test_bipagem_rejeita_produto_de_outro_segmento(self):
        self._autenticar(self.usuario_lub)
        self.client.post('/api/separacao/iniciar/', {'tarefa_id': self.tarefa_rota_lub.id}, format='json')
        response = self.client.post(
            '/api/separacao/bipar/',
            {'tarefa_id': self.tarefa_rota_lub.id, 'codigo': self.produto_filtro.cod_prod},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('não corresponde ao item esperado', response.data['erro'])

    def test_filtro_nao_permita_finalizar_concluido_com_pendencia(self):
        self._autenticar(self.usuario_filtro)
        self.client.post('/api/separacao/iniciar/', {'tarefa_id': self.tarefa_filtro.id}, format='json')
        response = self.client.post(
            '/api/separacao/finalizar/',
            {'tarefa_id': self.tarefa_filtro.id, 'status': Tarefa.Status.CONCLUIDO},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('NF de filtros com item faltante', response.data['erro'])

    def test_fechamento_com_restricao_exige_motivo(self):
        self._autenticar(self.usuario_ne)
        self.client.post('/api/separacao/iniciar/', {'tarefa_id': self.tarefa_rota_ne.id}, format='json')
        response_sem_motivo = self.client.post(
            '/api/separacao/finalizar/',
            {'tarefa_id': self.tarefa_rota_ne.id, 'status': Tarefa.Status.FECHADO_COM_RESTRICAO},
            format='json',
        )
        response_com_motivo = self.client.post(
            '/api/separacao/finalizar/',
            {
                'tarefa_id': self.tarefa_rota_ne.id,
                'status': Tarefa.Status.FECHADO_COM_RESTRICAO,
                'motivo': 'FALTA ITEM',
            },
            format='json',
        )

        self.assertEqual(response_sem_motivo.status_code, 400)
        self.assertEqual(response_sem_motivo.data, {'erro': 'Motivo da restricao e obrigatorio'})
        self.assertEqual(response_com_motivo.status_code, 200)
        self.assertEqual(response_com_motivo.data['status'], Tarefa.Status.FECHADO_COM_RESTRICAO)

    def test_item_com_restricao_bloqueia_nf_inteira(self):
        self._autenticar(self.usuario_ne)
        self.client.post('/api/separacao/iniciar/', {'tarefa_id': self.tarefa_rota_ne.id}, format='json')

        response = self.client.post(
            '/api/separacao/finalizar/',
            {
                'tarefa_id': self.tarefa_rota_ne.id,
                'status': Tarefa.Status.FECHADO_COM_RESTRICAO,
                'motivo': 'FALTA ITEM',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.nf.refresh_from_db()
        self.assertEqual(self.nf.status, NotaFiscal.Status.BLOQUEADA_COM_RESTRICAO)
        self.assertTrue(self.nf.bloqueada)

    def test_tarefa_liberada_permita_concluir_com_pendencia(self):
        self._autenticar(self.usuario_ne)
        self.tarefa_rota_ne.status = Tarefa.Status.LIBERADO_COM_RESTRICAO
        self.tarefa_rota_ne.save(update_fields=['status', 'updated_at'])

        response = self.client.post(
            '/api/separacao/finalizar/',
            {'tarefa_id': self.tarefa_rota_ne.id, 'status': Tarefa.Status.CONCLUIDO},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.tarefa_rota_ne.refresh_from_db()
        self.assertEqual(self.tarefa_rota_ne.status, Tarefa.Status.CONCLUIDO_COM_RESTRICAO)