from django.test import Client, TestCase, override_settings

from apps.clientes.models import Cliente
from apps.nf.models import NotaFiscal, NotaFiscalItem
from apps.produtos.models import Produto
from apps.rotas.models import Rota
from apps.tarefas.models import Tarefa, TarefaItem
from apps.usuarios.models import Setor, Usuario


@override_settings(ROOT_URLCONF='config.urls')
class MenuRegressionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.usuario = Usuario.objects.create_user(
            username='gestor_menu',
            nome='Gestor Menu',
            perfil=Usuario.Perfil.GESTOR,
            setores=[Setor.Codigo.FILTROS, Setor.Codigo.NAO_ENCONTRADO],
            password='123456',
            is_active=True,
        )
        self.client.login(username='gestor_menu', password='123456')

        self.rota = Rota.objects.create(nome='M01', cep_inicial='01000000', cep_final='01999999')
        self.cliente = Cliente.objects.create(nome='Cliente Menu', inscricao_estadual='111222333')
        self.produto = Produto.objects.create(
            cod_prod='123223',
            descricao='Produto Menu',
            cod_ean='789123223',
            categoria=Produto.Categoria.FILTROS,
        )
        self.nf = NotaFiscal.objects.create(
            chave_nfe='35111111111111111111550010000000011000000556',
            numero='1410290',
            cliente=self.cliente,
            rota=self.rota,
            status=NotaFiscal.Status.PENDENTE,
            data_emissao='2026-04-24T10:00:00-03:00',
            status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
            bloqueada=False,
            ativa=True,
        )
        NotaFiscalItem.objects.create(nf=self.nf, produto=self.produto, quantidade='10.00')
        self.tarefa = Tarefa.objects.create(
            nf=None,
            tipo=Tarefa.Tipo.ROTA,
            setor=Setor.Codigo.FILTROS,
            rota=self.rota,
            status=Tarefa.Status.ABERTO,
        )
        TarefaItem.objects.create(
            tarefa=self.tarefa,
            nf=self.nf,
            produto=self.produto,
            quantidade_total='10.00',
            quantidade_separada='3.00',
        )

    def test_layout_base_renderiza_estrutura_estavel_do_menu_mobile(self):
        response = self.client.get('/dashboard/separacao/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="toggleMenu"', html=False)
        self.assertContains(response, 'aria-controls="siteNav"', html=False)
        self.assertContains(response, 'id="menuBackdrop"', html=False)
        self.assertContains(response, 'id="siteNav"', html=False)
        self.assertContains(response, 'data-submenu-toggle', html=False)
        self.assertContains(response, '/separacao/', html=False)
        self.assertContains(response, '/conferencia/', html=False)
        self.assertContains(response, '/dashboard/separacao/', html=False)
        self.assertContains(response, '/logout/', html=False)

    def test_layout_base_mantem_submenu_operacao_aberto_na_rota_ativa(self):
        response = self.client.get('/dashboard/separacao/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'class="nav-group nav-item dropdown nav-group--open"',
            html=False,
        )
        self.assertContains(
            response,
            'data-submenu-toggle aria-expanded="true"',
            html=False,
        )
        self.assertContains(
            response,
            'href="/dashboard/separacao/" class="active"',
            html=False,
        )
