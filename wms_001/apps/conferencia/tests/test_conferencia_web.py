from django.test import Client, TestCase, override_settings

from apps.clientes.models import Cliente
from apps.conferencia.models import Conferencia, ConferenciaItem
from apps.nf.models import NotaFiscal, NotaFiscalItem
from apps.produtos.models import Produto
from apps.rotas.models import Rota
from apps.tarefas.models import Tarefa, TarefaItem
from apps.usuarios.models import Setor, Usuario


@override_settings(ROOT_URLCONF='config.urls')
class ConferenciaWebTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.usuario = Usuario.objects.create_user(
            username='conferente_web',
            nome='Conferente Web',
            perfil=Usuario.Perfil.CONFERENTE,
            setores=[Setor.Codigo.FILTROS],
            password='123456',
            is_active=True,
        )
        self.client.login(username='conferente_web', password='123456')

        self.rota = Rota.objects.create(nome='Rota Web', cep_inicial='01000000', cep_final='01999999')
        self.cliente = Cliente.objects.create(nome='Cliente Web', inscricao_estadual='123123123')
        self.produto = Produto.objects.create(
            cod_prod='WEB001',
            descricao='Produto Web',
            cod_ean='7895001',
            categoria=Produto.Categoria.FILTROS,
        )
        self.nf = NotaFiscal.objects.create(
            chave_nfe='35111111111111111111550010000000011000000123',
            numero='1410289',
            cliente=self.cliente,
            rota=self.rota,
            data_emissao='2026-04-24T10:00:00-03:00',
            status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
            bloqueada=False,
            ativa=True,
        )
        NotaFiscalItem.objects.create(nf=self.nf, produto=self.produto, quantidade='10.00')
        self.tarefa = Tarefa.objects.create(
            nf=self.nf,
            tipo=Tarefa.Tipo.FILTRO,
            setor=Setor.Codigo.FILTROS,
            rota=self.rota,
            status=Tarefa.Status.CONCLUIDO,
        )
        TarefaItem.objects.create(
            tarefa=self.tarefa,
            produto=self.produto,
            quantidade_total='10.00',
            quantidade_separada='10.00',
        )

    def _iniciar(self):
        response = self.client.post(f'/conferencia/{self.nf.id}/', {'acao': 'iniciar'})
        self.assertEqual(response.status_code, 302)
        return Conferencia.objects.get(nf=self.nf, conferente=self.usuario)

    def test_tela_execucao_exibe_item_atual(self):
        conferencia = self._iniciar()

        response = self.client.get(f'/conferencia/{self.nf.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1410289')
        self.assertContains(response, 'WEB001')
        self.assertContains(response, '10,00')
        self.assertContains(response, '0,00')
        self.assertEqual(response.context['conferencia_ativa'].id, conferencia.id)

    def test_bipagem_atualiza_quantidade_e_feedback(self):
        self._iniciar()

        response_post = self.client.post(
            f'/conferencia/{self.nf.id}/',
            {'acao': 'bipar', 'codigo': self.produto.cod_ean},
        )
        self.assertEqual(response_post.status_code, 302)

        response_get = self.client.get(f'/conferencia/{self.nf.id}/')
        item = ConferenciaItem.objects.get(conferencia__nf=self.nf, produto=self.produto)

        self.assertEqual(item.qtd_conferida, 1)
        self.assertContains(response_get, '1 / 10')

    def test_divergencia_exige_motivo_e_salva(self):
        conferencia = self._iniciar()
        item = ConferenciaItem.objects.get(conferencia=conferencia, produto=self.produto)

        response_sem_motivo = self.client.post(
            f'/conferencia/divergencia/{item.id}/',
            {'observacao': 'faltou item'},
        )
        self.assertEqual(response_sem_motivo.status_code, 200)
        self.assertContains(response_sem_motivo, 'Motivo da divergencia e obrigatorio')

        response_ok = self.client.post(
            f'/conferencia/divergencia/{item.id}/',
            {'motivo': ConferenciaItem.MotivoDivergencia.FALTA, 'observacao': 'faltou item'},
        )
        self.assertEqual(response_ok.status_code, 302)

        item.refresh_from_db()
        self.assertEqual(item.status, ConferenciaItem.Status.DIVERGENCIA)
        self.assertEqual(item.motivo_divergencia, ConferenciaItem.MotivoDivergencia.FALTA)

    def test_nao_finaliza_com_item_pendente_sem_decisao(self):
        self._iniciar()

        response = self.client.post(f'/conferencia/{self.nf.id}/', {'acao': 'finalizar_restricao'})

        self.assertEqual(response.status_code, 302)
        item = ConferenciaItem.objects.get(conferencia__nf=self.nf, produto=self.produto)
        self.assertIn(f'/conferencia/divergencia/{item.id}/', response.url)

    def test_finaliza_conferencia_liberada_com_pendencia(self):
        conferencia = Conferencia.objects.create(
            nf=self.nf,
            conferente=self.usuario,
            status=Conferencia.Status.LIBERADO_COM_RESTRICAO,
        )
        ConferenciaItem.objects.create(
            conferencia=conferencia,
            produto=self.produto,
            qtd_esperada='10.00',
            qtd_conferida='8.00',
            status=ConferenciaItem.Status.AGUARDANDO,
        )
        self.nf.status = NotaFiscal.Status.LIBERADA_COM_RESTRICAO
        self.nf.bloqueada = False
        self.nf.save(update_fields=['status', 'bloqueada', 'updated_at'])

        response = self.client.post(f'/conferencia/{self.nf.id}/', {'acao': 'finalizar_restricao'})

        self.assertEqual(response.status_code, 302)
        conferencia.refresh_from_db()
        self.assertEqual(conferencia.status, Conferencia.Status.CONCLUIDO_COM_RESTRICAO)