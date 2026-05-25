"""Armazenagem TEMP → estoque físico endereçado (registro único por operação)."""

from __future__ import annotations

import logging

from django.db import transaction

from apps.estoque.models import EstoqueFisico
from apps.estoque.services.fifo import formatar_fifo_nf
from apps.estoque.services.posicao import PosicaoEstoqueError, resolver_posicao
from apps.produtos.models import Produto
from apps.recebimento.models import EstoqueTemporario

logger = logging.getLogger(__name__)


class ArmazenagemError(Exception):
    pass


def armazenar_item_temp(*, temp_id: int, posicao_entrada: str, usuario) -> EstoqueFisico:
    with transaction.atomic():
        temp = (
            EstoqueTemporario.objects.select_for_update()
            .filter(pk=temp_id, status=EstoqueTemporario.Status.TEMP)
            .first()
        )
        if not temp:
            raise ArmazenagemError('Item TEMP não encontrado ou já armazenado.')

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
            quantidade=temp.quantidade,
            posicao=posicao,
            fifo_nf=fifo,
            data_entrada=data_entrada,
            nf_entrada=temp.nf_numero,
            chave_nfe=temp.chave_nfe,
            estoque_temporario=temp,
            usuario_armazenagem=usuario,
            status=EstoqueFisico.Status.ATIVO,
        )

        temp.status = EstoqueTemporario.Status.RESGATADO
        temp.save(update_fields=['status', 'updated_at'])

    logger.info(
        'ARMAZENAGEM_OK temp_id=%s estoque_id=%s produto=%s posicao=%s fifo=%s user_id=%s',
        temp_id,
        estoque.id,
        temp.produto_codigo,
        posicao.codigo_posicao,
        fifo,
        getattr(usuario, 'id', None),
    )
    return estoque
