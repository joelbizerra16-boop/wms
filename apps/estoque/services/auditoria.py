"""Indicadores de auditoria do estoque físico."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.estoque.models import EstoqueFisico, PosicaoEstoque

DIAS_FIFO_PARADO = 60
DIAS_PRODUTO_ANTIGO = 90


def coletar_indicadores_auditoria() -> list[dict]:
    agora = timezone.now()
    itens: list[dict] = []

    bloqueados = EstoqueFisico.objects.filter(status=EstoqueFisico.Status.BLOQUEADO).count()
    if bloqueados:
        itens.append(
            {
                'nivel': 'warning',
                'titulo': 'Estoque bloqueado',
                'detalhe': f'{bloqueados} linha(s) com status BLOQUEADO.',
            }
        )

    zeradas = EstoqueFisico.objects.filter(quantidade__lte=0).count()
    if zeradas:
        itens.append(
            {
                'nivel': 'danger',
                'titulo': 'Saldo zerado inconsistente',
                'detalhe': f'{zeradas} registro(s) com quantidade <= 0.',
            }
        )

    limite_parado = agora - timedelta(days=DIAS_FIFO_PARADO)
    parados = EstoqueFisico.objects.filter(
        status=EstoqueFisico.Status.ATIVO,
        quantidade__gt=0,
        data_entrada__lt=limite_parado,
    ).count()
    if parados:
        itens.append(
            {
                'nivel': 'warning',
                'titulo': f'FIFO parado (+{DIAS_FIFO_PARADO} dias)',
                'detalhe': f'{parados} linha(s) ativas sem movimentação há muito tempo.',
            }
        )

    limite_antigo = agora - timedelta(days=DIAS_PRODUTO_ANTIGO)
    antigos = EstoqueFisico.objects.filter(
        status=EstoqueFisico.Status.ATIVO,
        quantidade__gt=0,
        data_entrada__lt=limite_antigo,
    ).count()
    if antigos:
        itens.append(
            {
                'nivel': 'info',
                'titulo': f'Produtos antigos (+{DIAS_PRODUTO_ANTIGO} dias)',
                'detalhe': f'{antigos} linha(s) em estoque há mais de {DIAS_PRODUTO_ANTIGO} dias.',
            }
        )

    pos_bloqueadas = PosicaoEstoque.objects.filter(status=PosicaoEstoque.Status.BLOQUEADA, ativo=True).count()
    if pos_bloqueadas:
        itens.append(
            {
                'nivel': 'warning',
                'titulo': 'Posições bloqueadas',
                'detalhe': f'{pos_bloqueadas} endereço(s) com status BLOQUEADA.',
            }
        )

    pulmao_picking = EstoqueFisico.objects.filter(
        status=EstoqueFisico.Status.ATIVO,
        quantidade__gt=0,
        posicao__andar='1',
    ).count()
    if pulmao_picking:
        itens.append(
            {
                'nivel': 'info',
                'titulo': 'Saldo em pulmão (andar 1)',
                'detalhe': f'{pulmao_picking} linha(s) no andar 1 — candidatas a reabastecimento.',
            }
        )

    if not itens:
        itens.append(
            {
                'nivel': 'success',
                'titulo': 'Sem alertas críticos',
                'detalhe': 'Nenhuma inconsistência prioritária detectada no momento.',
            }
        )

    return itens


def saldo_produto_posicao(codigo_produto: str, posicao_label: str) -> Decimal:
    from apps.estoque.services.posicao import PosicaoEstoqueError, resolver_posicao

    try:
        pos = resolver_posicao(posicao_label)
    except PosicaoEstoqueError:
        return Decimal('0')
    return _saldo_disponivel_local(codigo_produto, pos)


def _saldo_disponivel_local(codigo_produto: str, posicao: PosicaoEstoque) -> Decimal:
    qs = EstoqueFisico.objects.filter(
        codigo_produto=codigo_produto,
        posicao=posicao,
        status=EstoqueFisico.Status.ATIVO,
        quantidade__gt=0,
    )
    return sum((linha.quantidade for linha in qs), Decimal('0'))
