from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.clientes.models import Cliente
from apps.logs.models import Log
from apps.nf.models import NotaFiscal
from apps.produtos.models import Produto
from apps.rotas.models import Rota
from apps.tarefas.models import Tarefa, TarefaItem
from apps.usuarios.models import Usuario


@override_settings(ROOT_URLCONF='config.urls')
class SeparacaoCanceladaAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.usuario = Usuario.objects.create_user(
            username='separador_api',
            nome='Separador API',
            perfil=Usuario.Perfil.SEPARADOR,
            setor=Usuario.Setor.LUBRIFICANTE,
            password='123456',
            is_active=True,
        )
        self.client.force_authenticate(self.usuario)
        self.rota = Rota.objects.create(nome='Rota Sep', cep_inicial='01000000', cep_final='01999999')
        self.cliente = Cliente.objects.create(nome='Cliente Sep', inscricao_estadual='111222333')
        self.produto = Produto.objects.create(
            cod_prod='SEP001',
            descricao='Produto Sep',
            cod_ean='789111',
            categoria=Produto.Categoria.LUBRIFICANTE,
        )
        self.nf_autorizada = NotaFiscal.objects.create(
            chave_nfe='35111111111111111111550010000000011000000031',
            numero='900',
            cliente=self.cliente,
            rota=self.rota,
            data_emissao='2026-04-23T10:00:00-03:00',
            status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
            bloqueada=False,
            ativa=True,
        )
        self.nf_cancelada = NotaFiscal.objects.create(
            chave_nfe='35111111111111111111550010000000011000000032',
            numero='901',
            cliente=self.cliente,
            rota=self.rota,
            data_emissao='2026-04-23T10:00:00-03:00',
            status_fiscal=NotaFiscal.StatusFiscal.CANCELADA,
            bloqueada=True,
            ativa=False,
        )
        self.tarefa_ok = Tarefa.objects.create(
            nf=self.nf_autorizada,
            tipo=Tarefa.Tipo.FILTRO,
            setor=Usuario.Setor.LUBRIFICANTE,
            rota=self.rota,
            status=Tarefa.Status.ABERTO,
        )
        self.tarefa_cancelada = Tarefa.objects.create(
            nf=self.nf_cancelada,
            tipo=Tarefa.Tipo.FILTRO,
            setor=Usuario.Setor.LUBRIFICANTE,
            rota=self.rota,
            status=Tarefa.Status.ABERTO,
        )
        TarefaItem.objects.create(tarefa=self.tarefa_ok, produto=self.produto, quantidade_total='2.00', quantidade_separada='0.00')
        TarefaItem.objects.create(tarefa=self.tarefa_cancelada, produto=self.produto, quantidade_total='2.00', quantidade_separada='0.00')

    def test_nf_autorizada_permite_separacao(self):
        response = self.client.post('/api/separacao/iniciar/', {'tarefa_id': self.tarefa_ok.id}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], Tarefa.Status.EM_EXECUCAO)
        self.tarefa_ok.refresh_from_db()
        self.assertEqual(self.tarefa_ok.usuario_id, self.usuario.id)

    def test_nf_cancelada_bloqueia_inicio_separacao(self):
        response = self.client.post('/api/separacao/iniciar/', {'tarefa_id': self.tarefa_cancelada.id}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'erro': 'NF cancelada não pode ser processada'})

    def test_nf_cancelada_nao_aparece_na_lista_de_tarefas(self):
        response = self.client.get('/api/separacao/tarefas/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], self.tarefa_ok.id)

    def test_tentativa_bloqueada_registra_log(self):
        self.client.post('/api/separacao/iniciar/', {'tarefa_id': self.tarefa_cancelada.id}, format='json')

        self.assertTrue(Log.objects.filter(acao='SEPARACAO BLOQUEADA', detalhe__contains='NF CANCELADA').exists())