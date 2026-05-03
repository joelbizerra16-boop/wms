from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from apps.clientes.models import Cliente
from apps.nf.models import NotaFiscal, NotaFiscalItem
from apps.produtos.models import Produto
from apps.rotas.models import Rota
from apps.tarefas.models import Tarefa, TarefaItem
from apps.usuarios.models import Setor, Usuario


@override_settings(ROOT_URLCONF='config.urls')
class AcessoPorPerfilTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.rota = Rota.objects.create(nome='R-Perfil', cep_inicial='01000000', cep_final='01999999')
        self.cliente = Cliente.objects.create(nome='Cliente Perfil', inscricao_estadual='123456789')
        self.produto = Produto.objects.create(
            cod_prod='PERFIL001',
            descricao='Produto Perfil',
            cod_ean='789000111',
            categoria=Produto.Categoria.LUBRIFICANTE,
        )
        self.nf = NotaFiscal.objects.create(
            chave_nfe='35111111111111111111550010000000011000000444',
            numero='200100',
            cliente=self.cliente,
            rota=self.rota,
            data_emissao='2026-04-24T10:00:00-03:00',
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
            status=Tarefa.Status.ABERTO,
        )
        TarefaItem.objects.create(
            tarefa=self.tarefa,
            produto=self.produto,
            quantidade_total='2.00',
            quantidade_separada='0.00',
        )
        self.separador = Usuario.objects.create_user(
            username='separador',
            nome='Separador',
            perfil=Usuario.Perfil.SEPARADOR,
            setores=[Setor.Codigo.LUBRIFICANTE],
            password='123456',
            is_active=True,
        )
        self.conferente = Usuario.objects.create_user(
            username='conferente',
            nome='Conferente',
            perfil=Usuario.Perfil.CONFERENTE,
            setores=[Setor.Codigo.FILTROS],
            password='123456',
            is_active=True,
        )
        self.gestor = Usuario.objects.create_user(
            username='gestor',
            nome='Gestor',
            perfil=Usuario.Perfil.GESTOR,
            setores=[Setor.Codigo.NAO_ENCONTRADO],
            password='123456',
            is_active=True,
        )

    def test_login_redireciona_separador_para_separacao(self):
        response = self.client.post('/login/', {'username': 'separador', 'password': '123456'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/separacao/')

    def test_login_redireciona_conferente_para_conferencia(self):
        response = self.client.post('/login/', {'username': 'conferente', 'password': '123456'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/conferencia/')

    def test_login_redireciona_gestor_para_home(self):
        response = self.client.post('/login/', {'username': 'gestor', 'password': '123456'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/home/')

    def test_separador_nao_acessa_conferencia_nem_gestao(self):
        self.client.login(username='separador', password='123456')

        response_conferencia = self.client.get('/conferencia/')
        response_dashboard = self.client.get('/dashboard/separacao/')
        response_home = self.client.get('/home/')

        self.assertEqual(response_conferencia.status_code, 302)
        self.assertEqual(response_conferencia.url, '/separacao/')
        self.assertEqual(response_dashboard.status_code, 302)
        self.assertEqual(response_dashboard.url, '/separacao/')
        self.assertEqual(response_home.status_code, 302)
        self.assertEqual(response_home.url, '/separacao/')

    def test_conferente_nao_acessa_separacao_nem_gestao(self):
        self.client.login(username='conferente', password='123456')

        response_separacao = self.client.get('/separacao/')
        response_importar = self.client.get('/importar/')
        response_home = self.client.get('/home/')

        self.assertEqual(response_separacao.status_code, 302)
        self.assertEqual(response_separacao.url, '/conferencia/')
        self.assertEqual(response_importar.status_code, 302)
        self.assertEqual(response_importar.url, '/conferencia/')
        self.assertEqual(response_home.status_code, 302)
        self.assertEqual(response_home.url, '/conferencia/')

    def test_gestor_ve_menu_principal_completo(self):
        self.client.login(username='gestor', password='123456')

        response = self.client.get('/home/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Dashboard')
        self.assertContains(response, 'Dash Separação')
        self.assertContains(response, 'Usuários')

    def test_separador_ve_apenas_menu_de_separacao(self):
        self.client.login(username='separador', password='123456')

        response = self.client.get('/separacao/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Separação')
        self.assertNotContains(response, 'Conferência')
        self.assertNotContains(response, 'Usuários')

    def test_api_respeita_perfil_na_separacao_e_conferencia(self):
        self.client.login(username='separador', password='123456')
        response_tarefa = self.client.get('/api/status/tarefa/999/')
        response_nf = self.client.get(f'/api/status/nf/{self.nf.id}/')

        self.assertIn(response_tarefa.status_code, {403, 404})
        self.assertEqual(response_nf.status_code, 403)


@override_settings(ROOT_URLCONF='config.urls')
class MonitoramentoUsuariosOnlineTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.gestor = Usuario.objects.create_user(
            username='gestor_online',
            nome='Gestor Online',
            perfil=Usuario.Perfil.GESTOR,
            setores=[Setor.Codigo.NAO_ENCONTRADO],
            password='123456',
            is_active=True,
            is_staff=True,
        )
        self.operacional = Usuario.objects.create_user(
            username='conf_online',
            nome='Conferente Online',
            perfil=Usuario.Perfil.CONFERENTE,
            setores=[Setor.Codigo.FILTROS],
            password='123456',
            is_active=True,
        )

    def test_status_online_usa_last_activity_recente(self):
        self.operacional.last_activity = timezone.now()
        self.operacional.save(update_fields=['last_activity', 'updated_at'])
        self.client.login(username='gestor_online', password='123456')
        response = self.client.get('/usuarios/logados/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ONLINE')

    def test_status_offline_quando_atividade_expirada(self):
        self.operacional.last_activity = timezone.now() - timedelta(minutes=6)
        self.operacional.save(update_fields=['last_activity', 'updated_at'])
        self.client.login(username='gestor_online', password='123456')
        response = self.client.get('/usuarios/logados/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'OFFLINE')


@override_settings(ROOT_URLCONF='config.urls')
class IntegridadeMultiSetorUsuarioTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.gestor = Usuario.objects.create_user(
            username='gestor_multi_setor',
            nome='Gestor Multi Setor',
            perfil=Usuario.Perfil.GESTOR,
            setores=[Setor.Codigo.LUBRIFICANTE],
            password='123456',
            is_active=True,
        )
        self.alvo = Usuario.objects.create_user(
            username='alvo_multi_setor',
            nome='Alvo Multi Setor',
            perfil=Usuario.Perfil.SEPARADOR,
            setores=[Setor.Codigo.LUBRIFICANTE],
            password='123456',
            is_active=True,
        )

    def test_cadastro_usuario_exige_ao_menos_um_setor(self):
        self.client.login(username='gestor_multi_setor', password='123456')
        response = self.client.post(
            '/usuarios/',
            {
                'nome': 'Sem Setor',
                'username': 'sem_setor_novo',
                'senha': '123456',
                'perfil': Usuario.Perfil.SEPARADOR,
                'is_active': 'on',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Usuario.objects.filter(username='sem_setor_novo').exists())
        self.assertContains(response, 'Selecione pelo menos um setor')

    def test_edicao_usuario_nao_permte_remover_todos_os_setores(self):
        self.client.login(username='gestor_multi_setor', password='123456')
        response = self.client.post(
            f'/usuarios/{self.alvo.id}/editar/',
            {
                'nome': self.alvo.nome,
                'username': self.alvo.username,
                'perfil': self.alvo.perfil,
                'is_active': 'on',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.alvo.refresh_from_db()
        self.assertTrue(self.alvo.setores.exists())
        self.assertContains(response, 'Selecione pelo menos um setor')
