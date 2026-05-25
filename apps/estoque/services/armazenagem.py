"""Armazenagem TEMP → estoque físico (parcial, múltiplas posições, um registro por movimento)."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction

from apps.estoque.models import EstoqueFisico
from apps.estoque.services.fifo import formatar_fifo_nf
from apps.estoque.services.posicao import PosicaoEstoqueError, resolver_posicao
from apps.produtos.models import Produto
from apps.recebimento.models import EstoqueTemporario

logger = logging.getLogger(__name__)


class ArmazenagemError(Exception):
    pass


def parse_quantidade_armazenagem(valor) -> Decimal:
    texto = str(valor or '').strip().replace(',', '.')
    if not texto:
        raise ArmazenagemError('Informe a quantidade para armazenar.')
    try:
        qtd = Decimal(texto)
    except (InvalidOperation, ValueError) as exc:
        raise ArmazenagemError('Quantidade inválida.') from exc
    if qtd <= 0:
        raise ArmazenagemError('Quantidade deve ser maior que zero.')
    return qtd


def armazenar_item_temp(
    *,
    temp_id: int,
    posicao_entrada: str,
    quantidade: Decimal,
    usuario,
) -> EstoqueFisico:
    qtd_mov = parse_quantidade_armazenagem(quantidade)

    with transaction.atomic():
        temp = (
            EstoqueTemporario.objects.select_for_update()
            .filter(pk=temp_id, status=EstoqueTemporario.Status.TEMP)
            .first()
        )
        if not temp:
            raise ArmazenagemError('Item TEMP não encontrado ou já finalizado.')

        if temp.quantidade <= 0:
            raise ArmazenagemError('Saldo TEMP esgotado.')

        if qtd_mov > temp.quantidade:
            raise ArmazenagemError(
                f'Quantidade maior que o saldo TEMP ({temp.quantidade.normalize()} disponível).'
            )

        try:
            posicao = resolver_posicao(posicao_entrada)
        except PosicaoEstoqueError as exc:
            raise ArmazenagemError(str(exc)) from exc

        produto = Produto.objects.filter(cod_prod=temp.produto_codigo).first()
        data_entrada = temp.data_recebimento
        fifo = formatar_fifo_nf(data_entrada, temp.nf_numero)

        estoque = EstoqueFisico.objects.create(
            produto=produto,
            codigo_produto=temp.produto_codigo,
            descricao=temp.descricao,
            quantidade=qtd_mov,
            posicao=posicao,
            fifo_nf=fifo,
            data_entrada=data_entrada,
            nf_entrada=temp.nf_numero,
            chave_nfe=temp.chave_nfe,
            estoque_temporario=temp,
            usuario_armazenagem=usuario,
            status=EstoqueFisico.Status.ATIVO,
        )

        temp.quantidade -= qtd_mov
        campos_update = ['quantidade', 'updated_at']
        finalizado = temp.quantidade <= 0
        if finalizado:
            temp.quantidade = Decimal('0')
            temp.status = EstoqueTemporario.Status.RESGATADO
            campos_update.append('status')
        temp.save(update_fields=campos_update)

    logger.info(
        'ARMAZENAGEM_MOV temp_id=%s estoque_id=%s produto=%s posicao=%s qtd=%s saldo_temp=%s '
        'fifo=%s nf=%s user_id=%s finalizado=%s',
        temp_id,
        estoque.id,
        temp.produto_codigo,
        posicao.codigo_posicao,
        qtd_mov,
        temp.quantidade,
        fifo,
        temp.nf_numero,
        getattr(usuario, 'id', None),
        finalizado,
    )
    return estoque
