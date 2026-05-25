from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.estoque.models import EstoqueFisico, MovimentacaoEstoque, PosicaoEstoque
from apps.estoque.services.movimentacao import (
    MovimentacaoError,
    bloquear_estoque,
    reabastecer_estoque,
    transferir_estoque,
)
User = get_user_model()


class MovimentacaoTransferenciaTestCase(TestCase):
    def setUp(self):
        self.gestor = User.objects.create_user(
            username='gestor_mov',
            password='x',
            nome='Gestor',
            perfil=User.Perfil.GESTOR,
            setor=User.Setor.FILTROS,
        )
        self.pulmao = PosicaoEstoque.objects.create(
            codigo_posicao='1-1-1-1',
            rua='1',
            posicao='1',
            andar='1',
            lado='1',
        )
        self.picking = PosicaoEstoque.objects.create(
            codigo_posicao='1-1-2-1',
            rua='1',
            posicao='1',
            andar='2',
            lado='1',
        )
        self.picking2 = PosicaoEstoque.objects.create(
            codigo_posicao='1-1-3-1',
            rua='1',
            posicao='1',
            andar='3',
            lado='1',
        )
        EstoqueFisico.objects.create(
            codigo_produto='20005',
            descricao='ARLA 32',
            quantidade=Decimal('540'),
            posicao=self.pulmao,
            fifo_nf='05/26-377439',
            data_entrada=timezone.now(),
            nf_entrada='377439',
            usuario_armazenagem=self.gestor,
        )

    def test_transferencia_parcial_preserva_fifo(self):
        transferir_estoque(
            codigo_produto='20005',
            posicao_origem='1 1 1 1',
            posicao_destino='1 1 3 1',
            quantidade=Decimal('200'),
            usuario=self.gestor,
        )
        dest = EstoqueFisico.objects.get(posicao=self.picking2, quantidade=Decimal('200'))
        self.assertEqual(dest.fifo_nf, '05/26-377439')
        pulmao_saldo = EstoqueFisico.objects.get(posicao=self.pulmao, status=EstoqueFisico.Status.ATIVO)
        self.assertEqual(pulmao_saldo.quantidade, Decimal('340'))

    def test_reabastecimento_pulmao_para_picking(self):
        reabastecer_estoque(
            codigo_produto='20005',
            posicao_origem='1 1 1 1',
            posicao_destino='1 1 2 1',
            quantidade=Decimal('100'),
            usuario=self.gestor,
        )
        self.assertTrue(
            EstoqueFisico.objects.filter(posicao=self.picking, quantidade=Decimal('100')).exists()
        )

    def test_bloqueio_impede_transferencia(self):
        linha = EstoqueFisico.objects.filter(posicao=self.pulmao).first()
        bloquear_estoque(usuario=self.gestor, estoque_id=linha.id, motivo=MovimentacaoEstoque.Motivo.QUARENTENA)
        with self.assertRaises(MovimentacaoError):
            transferir_estoque(
                codigo_produto='20005',
                posicao_origem='1 1 1 1',
                posicao_destino='1 1 2 1',
                quantidade=Decimal('10'),
                usuario=self.gestor,
            )

    def test_historico_registra_transferencia(self):
        transferir_estoque(
            codigo_produto='20005',
            posicao_origem='1 1 1 1',
            posicao_destino='1 1 2 1',
            quantidade=Decimal('50'),
            usuario=self.gestor,
        )
        self.assertEqual(
            MovimentacaoEstoque.objects.filter(tipo=MovimentacaoEstoque.Tipo.TRANSFERENCIA).count(),
            1,
        )
