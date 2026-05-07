from time import perf_counter

from django.db import connection, reset_queries
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.clientes.models import Cliente
from apps.conferencia.models import Conferencia, ConferenciaItem
from apps.nf.models import NotaFiscal, NotaFiscalItem
from apps.produtos.models import Produto
from apps.rotas.models import Rota
from apps.tarefas.models import Tarefa, TarefaItem
from apps.tarefas.services.separacao_service import bipar_tarefa
from apps.usuarios.models import Setor, Usuario


@override_settings(ROOT_URLCONF='config.urls')
class PerformanceRegressionTests(TestCase):
    def setUp(self):
        self.gestor = Usuario.objects.create_user(
            username='gestor_perf',
            nome='Gestor Perf',
            perfil=Usuario.Perfil.GESTOR,
            setores=[Setor.Codigo.FILTROS, Setor.Codigo.AGREGADO],
            password='123456',
            is_active=True,
        )
        self.client = Client()
        self.client.login(username='gestor_perf', password='123456')

        self.rota = Rota.objects.create(nome='P01', cep_inicial='01000000', cep_final='01999999')
        self.cliente = Cliente.objects.create(nome='Cliente Perf', inscricao_estadual='99887766')
        self.produto_a = Produto.objects.create(
            cod_prod='PERF001',
            codigo='P001',
            descricao='Produto Performance A',
            cod_ean='789PERF001',
            setor=Setor.Codigo.FILTROS,
            categoria=Produto.Categoria.FILTROS,
        )
        self.produto_b = Produto.objects.create(
            cod_prod='PERF002',
            codigo='P002',
            descricao='Produto Performance B',
            cod_ean='789PERF002',
            setor=Setor.Codigo.FILTROS,
            categoria=Produto.Categoria.FILTROS,
        )

        self.nf = NotaFiscal.objects.create(
            chave_nfe='35111111111111111111550010000000011000009999',
            numero='1410999',
            cliente=self.cliente,
            rota=self.rota,
            status=NotaFiscal.Status.PENDENTE,
            data_emissao=timezone.now(),
            status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
            bloqueada=False,
            ativa=True,
        )
        NotaFiscalItem.objects.create(nf=self.nf, produto=self.produto_a, quantidade='3.00')
        NotaFiscalItem.objects.create(nf=self.nf, produto=self.produto_b, quantidade='2.00')

        self.tarefa = Tarefa.objects.create(
            nf=None,
            tipo=Tarefa.Tipo.ROTA,
            setor=Setor.Codigo.FILTROS,
            rota=self.rota,
            status=Tarefa.Status.EM_EXECUCAO,
            usuario=self.gestor,
            usuario_em_execucao=self.gestor,
        )
        TarefaItem.objects.create(
            tarefa=self.tarefa,
            nf=self.nf,
            produto=self.produto_a,
            quantidade_total='3.00',
            quantidade_separada='1.00',
        )
        TarefaItem.objects.create(
            tarefa=self.tarefa,
            nf=self.nf,
            produto=self.produto_b,
            quantidade_total='2.00',
            quantidade_separada='0.00',
        )

        self.conferencia = Conferencia.objects.create(
            nf=self.nf,
            conferente=self.gestor,
            status=Conferencia.Status.EM_CONFERENCIA,
        )
        ConferenciaItem.objects.create(
            conferencia=self.conferencia,
            produto=self.produto_a,
            qtd_esperada='3.00',
            qtd_conferida='1.00',
            status=ConferenciaItem.Status.AGUARDANDO,
        )
        ConferenciaItem.objects.create(
            conferencia=self.conferencia,
            produto=self.produto_b,
            qtd_esperada='2.00',
            qtd_conferida='0.00',
            status=ConferenciaItem.Status.AGUARDANDO,
        )

    def _measure(self, method, path, *, data=None, client=None):
        target_client = client or self.client
        reset_queries()
        with CaptureQueriesContext(connection) as captured:
            started_at = perf_counter()
            if method == 'GET':
                response = target_client.get(path)
            else:
                response = target_client.post(path, data or {})
            total_ms = (perf_counter() - started_at) * 1000
        db_ms = sum(float(query.get('time') or 0) for query in captured.captured_queries) * 1000
        metrics = {
            'total_ms': round(total_ms, 2),
            'db_ms': round(db_ms, 2),
            'queries': len(captured),
        }
        print(f'PERF {method} {path}: {metrics}')
        return response, metrics

    def test_perf_views_principais_ficam_dentro_do_orcamento(self):
        budgets = [
            ('GET', '/home/', 10),
            ('GET', '/produtos/', 8),
            ('GET', '/separacao/', 18),
            ('GET', '/conferencia/', 20),
        ]

        for method, path, max_queries in budgets:
            with self.subTest(path=path):
                response, metrics = self._measure(method, path)
                self.assertEqual(response.status_code, 200, metrics)
                self.assertLessEqual(metrics['queries'], max_queries, metrics)

    def test_perf_bipagem_separacao_reduz_queries_no_caminho_critico(self):
        reset_queries()
        with CaptureQueriesContext(connection) as captured:
            started_at = perf_counter()
            resultado = bipar_tarefa(self.tarefa.id, self.produto_b.cod_ean, self.gestor)
            total_ms = (perf_counter() - started_at) * 1000

        db_ms = sum(float(query.get('time') or 0) for query in captured.captured_queries) * 1000
        metrics = {
            'total_ms': round(total_ms, 2),
            'db_ms': round(db_ms, 2),
            'queries': len(captured),
        }
        print(f'PERF SERVICE bipar_tarefa: {metrics}')

        self.assertEqual(resultado['status'], 'ok', metrics)
        self.assertLessEqual(metrics['queries'], 30, metrics)
        self.assertLessEqual(metrics['total_ms'], 400, metrics)