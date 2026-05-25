from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.estoque.models import EstoqueFisico, PosicaoEstoque
from apps.estoque.services.armazenagem import ArmazenagemError, armazenar_item_temp
from apps.estoque.services.fifo import formatar_fifo_nf
from apps.estoque.services.posicao import resolver_posicao
from apps.recebimento.models import EstoqueTemporario

User = get_user_model()


class EstoqueFifoTestCase(TestCase):
    def test_formatar_fifo(self):
        data = timezone.datetime(2026, 5, 25, 10, 0, tzinfo=timezone.get_current_timezone())
        self.assertEqual(formatar_fifo_nf(data, '1414282'), '05/26-1414282')


class EstoquePosicaoTestCase(TestCase):
    def setUp(self):
        self.pos = PosicaoEstoque.objects.create(
            codigo_posicao='RUA-1-POS-1-A2-L1',
            rua='1',
            posicao='1',
            andar='2',
            lado='1',
        )

    def test_label_coletor(self):
        self.assertEqual(self.pos.label_coletor, '1 1 2 1')

    def test_andar_1_nao_apta_separacao(self):
        pulmao = PosicaoEstoque.objects.create(
            codigo_posicao='PULMAO',
            rua='PULMAO',
            posicao='1',
            andar='1',
            lado='1',
        )
        self.assertFalse(pulmao.apta_para_separacao())
        self.assertTrue(self.pos.apta_para_separacao())

    def test_resolver_por_coletor(self):
        encontrada = resolver_posicao('1 1 2 1')
        self.assertEqual(encontrada.pk, self.pos.pk)


class EstoqueArmazenagemTestCase(TestCase):
    def setUp(self):
        self.gestor = User.objects.create_user(
            username='gestor_est',
            password='x',
            nome='Gestor',
            perfil=User.Perfil.GESTOR,
            setor=User.Setor.FILTROS,
        )
        self.pos = PosicaoEstoque.objects.create(
            codigo_posicao='1-1-2-1',
            rua='1',
            posicao='1',
            andar='2',
            lado='1',
        )
        self.temp = EstoqueTemporario.objects.create(
            chave_nfe='1' * 44,
            nf_numero='377439',
            produto_codigo='20005',
            descricao='ARLA 32',
            quantidade=Decimal('540'),
            usuario_recebimento=self.gestor,
            status=EstoqueTemporario.Status.TEMP,
        )

    def test_armazenagem_parcial_duas_posicoes(self):
        pos2 = PosicaoEstoque.objects.create(
            codigo_posicao='1-1-3-1',
            rua='1',
            posicao='1',
            andar='3',
            lado='1',
        )
        e1 = armazenar_item_temp(
            temp_id=self.temp.id,
            posicao_entrada='1 1 2 1',
            quantidade=Decimal('200'),
            usuario=self.gestor,
        )
        self.temp.refresh_from_db()
        self.assertEqual(self.temp.status, EstoqueTemporario.Status.TEMP)
        self.assertEqual(self.temp.quantidade, Decimal('340'))
        self.assertEqual(e1.quantidade, Decimal('200'))

        e2 = armazenar_item_temp(
            temp_id=self.temp.id,
            posicao_entrada='1 1 3 1',
            quantidade=Decimal('340'),
            usuario=self.gestor,
        )
        self.temp.refresh_from_db()
        self.assertEqual(self.temp.status, EstoqueTemporario.Status.RESGATADO)
        self.assertEqual(self.temp.quantidade, Decimal('0'))
        self.assertEqual(e2.quantidade, Decimal('340'))
        self.assertEqual(EstoqueFisico.objects.count(), 2)
        self.assertEqual(e2.posicao_id, pos2.id)

    def test_rejeita_quantidade_maior_que_temp(self):
        with self.assertRaises(ArmazenagemError):
            armazenar_item_temp(
                temp_id=self.temp.id,
                posicao_entrada='1 1 2 1',
                quantidade=Decimal('541'),
                usuario=self.gestor,
            )

    def test_nao_rearmazena_temp_finalizado(self):
        armazenar_item_temp(
            temp_id=self.temp.id,
            posicao_entrada='1 1 2 1',
            quantidade=Decimal('540'),
            usuario=self.gestor,
        )
        with self.assertRaises(ArmazenagemError):
            armazenar_item_temp(
                temp_id=self.temp.id,
                posicao_entrada='1 1 2 1',
                quantidade=Decimal('1'),
                usuario=self.gestor,
            )


class EstoqueViewsWebTestCase(TestCase):
    def setUp(self):
        self.gestor = User.objects.create_user(
            username='gestor_est_web',
            password='x',
            nome='Gestor',
            perfil=User.Perfil.GESTOR,
            setor=User.Setor.FILTROS,
        )
        self.client = Client()
        self.client.force_login(self.gestor)

    def test_telas_estoque_200(self):
        for name in (
            'web-estoque-lista',
            'web-estoque-posicoes',
            'web-estoque-armazenagem',
            'web-estoque-movimentacoes',
        ):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
