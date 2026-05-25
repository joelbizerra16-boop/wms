"""Movimentações internas: transferência, reabastecimento, ajustes e bloqueios."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.estoque.models import EstoqueFisico, MovimentacaoEstoque, PosicaoEstoque
from apps.estoque.services.quantidade import QuantidadeEstoqueError, parse_quantidade
from apps.estoque.services.fifo import formatar_fifo_nf
from apps.estoque.services.posicao import PosicaoEstoqueError, resolver_posicao
from apps.produtos.models import Produto

logger = logging.getLogger(__name__)


class MovimentacaoError(Exception):
    pass


def _andar_numero(posicao: PosicaoEstoque) -> int | None:
    try:
        return int(str(posicao.andar).strip())
    except (TypeError, ValueError):
        return None


def _saldo_disponivel(codigo_produto: str, posicao: PosicaoEstoque, fifo_nf: str = '') -> Decimal:
    qs = EstoqueFisico.objects.filter(
        codigo_produto=codigo_produto,
        posicao=posicao,
        status=EstoqueFisico.Status.ATIVO,
        quantidade__gt=0,
    )
    if fifo_nf:
        qs = qs.filter(fifo_nf=fifo_nf)
    total = sum((linha.quantidade for linha in qs), Decimal('0'))
    return total


def _registrar_mov(
    *,
    tipo: str,
    linha: EstoqueFisico | None,
    codigo_produto: str,
    descricao: str,
    quantidade: Decimal,
    usuario,
    posicao_origem=None,
    posicao_destino=None,
    motivo: str = '',
    observacao: str = '',
) -> MovimentacaoEstoque:
    mov = MovimentacaoEstoque.objects.create(
        tipo=tipo,
        produto=linha.produto if linha else None,
        codigo_produto=codigo_produto,
        descricao=descricao,
        estoque_fisico=linha,
        posicao_origem=posicao_origem,
        posicao_destino=posicao_destino,
        quantidade=quantidade,
        fifo_nf=linha.fifo_nf if linha else '',
        nf_entrada=linha.nf_entrada if linha else '',
        usuario=usuario,
        motivo=motivo,
        observacao=observacao,
        status=MovimentacaoEstoque.Status.CONFIRMADO,
    )
    logger.info(
        'MOVIMENTACAO_ESTOQUE tipo=%s id=%s produto=%s qtd=%s fifo=%s user_id=%s',
        tipo,
        mov.id,
        codigo_produto,
        quantidade,
        mov.fifo_nf,
        getattr(usuario, 'id', None),
    )
    return mov


def _reduzir_linha(linha: EstoqueFisico, qtd: Decimal):
    if qtd >= linha.quantidade:
        linha.quantidade = Decimal('0')
        linha.save(update_fields=['quantidade', 'updated_at'])
    else:
        linha.quantidade -= qtd
        linha.save(update_fields=['quantidade', 'updated_at'])


def _criar_linha_destino(linha_origem: EstoqueFisico, pos_dest: PosicaoEstoque, qtd: Decimal, usuario) -> EstoqueFisico:
    return EstoqueFisico.objects.create(
        produto=linha_origem.produto,
        codigo_produto=linha_origem.codigo_produto,
        descricao=linha_origem.descricao,
        quantidade=qtd,
        posicao=pos_dest,
        fifo_nf=linha_origem.fifo_nf,
        data_entrada=linha_origem.data_entrada,
        nf_entrada=linha_origem.nf_entrada,
        chave_nfe=linha_origem.chave_nfe,
        estoque_temporario=linha_origem.estoque_temporario,
        usuario_armazenagem=usuario,
        status=EstoqueFisico.Status.ATIVO,
    )


def _mover_quantidade(
    *,
    codigo_produto: str,
    pos_origem: PosicaoEstoque,
    pos_destino: PosicaoEstoque,
    quantidade: Decimal,
    usuario,
    tipo_mov: str,
    motivo: str = '',
    observacao: str = '',
    fifo_nf: str = '',
):
    if pos_origem.pk == pos_destino.pk:
        raise MovimentacaoError('Origem e destino devem ser posições diferentes.')

    linhas = list(
        EstoqueFisico.objects.select_for_update()
        .filter(
            codigo_produto=codigo_produto,
            posicao=pos_origem,
            status=EstoqueFisico.Status.ATIVO,
            quantidade__gt=0,
        )
        .order_by('data_entrada', 'id')
    )
    if fifo_nf:
        linhas = [l for l in linhas if l.fifo_nf == fifo_nf]

    if not linhas:
        raise MovimentacaoError('Nenhum saldo ativo encontrado na origem para este produto.')

    saldo = sum((l.quantidade for l in linhas), Decimal('0'))
    if quantidade > saldo:
        raise MovimentacaoError(f'Saldo insuficiente na origem ({saldo} disponível).')

    restante = quantidade
    descricao = linhas[0].descricao
    for linha in linhas:
        if restante <= 0:
            break
        if linha.status == EstoqueFisico.Status.BLOQUEADO:
            raise MovimentacaoError(f'Linha FIFO {linha.fifo_nf} está bloqueada.')
        qtd_mov = min(restante, linha.quantidade)
        nova = _criar_linha_destino(linha, pos_destino, qtd_mov, usuario)
        _reduzir_linha(linha, qtd_mov)
        _registrar_mov(
            tipo=tipo_mov,
            linha=nova,
            codigo_produto=codigo_produto,
            descricao=descricao,
            quantidade=qtd_mov,
            usuario=usuario,
            posicao_origem=pos_origem,
            posicao_destino=pos_destino,
            motivo=motivo,
            observacao=observacao,
        )
        restante -= qtd_mov


@transaction.atomic
def transferir_estoque(
    *,
    codigo_produto: str,
    posicao_origem: str,
    posicao_destino: str,
    quantidade,
    usuario,
    fifo_nf: str = '',
    observacao: str = '',
) -> None:
    codigo = (codigo_produto or '').strip()
    if not codigo:
        raise MovimentacaoError('Informe o código do produto.')
    try:
        qtd = parse_quantidade(quantidade)
    except QuantidadeEstoqueError as exc:
        raise MovimentacaoError(str(exc)) from exc
    try:
        origem = resolver_posicao(posicao_origem)
        destino = resolver_posicao(posicao_destino)
    except PosicaoEstoqueError as exc:
        raise MovimentacaoError(str(exc)) from exc

    _mover_quantidade(
        codigo_produto=codigo,
        pos_origem=origem,
        pos_destino=destino,
        quantidade=qtd,
        usuario=usuario,
        tipo_mov=MovimentacaoEstoque.Tipo.TRANSFERENCIA,
        motivo=MovimentacaoEstoque.Motivo.TRANSFERENCIA,
        observacao=observacao,
        fifo_nf=(fifo_nf or '').strip(),
    )


@transaction.atomic
def reabastecer_estoque(
    *,
    codigo_produto: str,
    posicao_origem: str,
    posicao_destino: str,
    quantidade,
    usuario,
    observacao: str = '',
) -> None:
    try:
        origem = resolver_posicao(posicao_origem)
        destino = resolver_posicao(posicao_destino)
    except PosicaoEstoqueError as exc:
        raise MovimentacaoError(str(exc)) from exc

    andar_origem = _andar_numero(origem)
    andar_destino = _andar_numero(destino)
    if andar_origem != 1:
        raise MovimentacaoError('Reabastecimento: origem deve ser andar 1 (pulmão).')
    if andar_destino is not None and andar_destino < 2:
        raise MovimentacaoError('Reabastecimento: destino deve ser andar >= 2 (picking).')

    _mover_quantidade(
        codigo_produto=(codigo_produto or '').strip(),
        pos_origem=origem,
        pos_destino=destino,
        quantidade=parse_quantidade(quantidade),
        usuario=usuario,
        tipo_mov=MovimentacaoEstoque.Tipo.REABASTECIMENTO,
        motivo=MovimentacaoEstoque.Motivo.REABASTECIMENTO,
        observacao=observacao,
    )


@transaction.atomic
def ajustar_estoque(
    *,
    codigo_produto: str,
    posicao_entrada: str,
    quantidade,
    usuario,
    motivo: str,
    observacao: str = '',
    positivo: bool = True,
) -> None:
    codigo = (codigo_produto or '').strip()
    if not codigo:
        raise MovimentacaoError('Informe o código do produto.')
    try:
        qtd = parse_quantidade(quantidade)
    except QuantidadeEstoqueError as exc:
        raise MovimentacaoError(str(exc)) from exc
    try:
        posicao = resolver_posicao(posicao_entrada)
    except PosicaoEstoqueError as exc:
        raise MovimentacaoError(str(exc)) from exc

    produto = Produto.objects.filter(cod_prod=codigo).first()
    descricao = produto.descricao if produto else codigo

    if not positivo:
        return ajustar_estoque_negativo(
            codigo_produto=codigo,
            posicao_entrada=posicao_entrada,
            quantidade=quantidade,
            usuario=usuario,
            motivo=motivo,
            observacao=observacao,
        )

    agora = timezone.now()
    fifo = formatar_fifo_nf(agora, 'AJUSTE')
    linha = EstoqueFisico.objects.create(
        produto=produto,
        codigo_produto=codigo,
        descricao=descricao,
        quantidade=qtd,
        posicao=posicao,
        fifo_nf=fifo,
        data_entrada=agora,
        nf_entrada='AJUSTE',
        usuario_armazenagem=usuario,
        status=EstoqueFisico.Status.ATIVO,
    )
    _registrar_mov(
        tipo=MovimentacaoEstoque.Tipo.AJUSTE,
        linha=linha,
        codigo_produto=codigo,
        descricao=descricao,
        quantidade=qtd,
        usuario=usuario,
        posicao_destino=posicao,
        motivo=motivo,
        observacao=observacao or 'Ajuste positivo',
    )


@transaction.atomic
def ajustar_estoque_negativo(
    *,
    codigo_produto: str,
    posicao_entrada: str,
    quantidade,
    usuario,
    motivo: str,
    observacao: str = '',
) -> None:
    codigo = (codigo_produto or '').strip()
    try:
        qtd = parse_quantidade(quantidade)
    except QuantidadeEstoqueError as exc:
        raise MovimentacaoError(str(exc)) from exc
    try:
        posicao = resolver_posicao(posicao_entrada)
    except PosicaoEstoqueError as exc:
        raise MovimentacaoError(str(exc)) from exc

    linhas = list(
        EstoqueFisico.objects.select_for_update()
        .filter(
            codigo_produto=codigo,
            posicao=posicao,
            status=EstoqueFisico.Status.ATIVO,
            quantidade__gt=0,
        )
        .order_by('data_entrada', 'id')
    )
    saldo = sum((l.quantidade for l in linhas), Decimal('0'))
    if qtd > saldo:
        raise MovimentacaoError(f'Saldo insuficiente ({saldo} disponível).')

    restante = qtd
    for linha in linhas:
        if restante <= 0:
            break
        qtd_mov = min(restante, linha.quantidade)
        _reduzir_linha(linha, qtd_mov)
        _registrar_mov(
            tipo=MovimentacaoEstoque.Tipo.AJUSTE,
            linha=linha,
            codigo_produto=codigo,
            descricao=linha.descricao,
            quantidade=qtd_mov,
            usuario=usuario,
            posicao_origem=posicao,
            motivo=motivo,
            observacao=observacao or 'Ajuste negativo',
        )
        restante -= qtd_mov


@transaction.atomic
def bloquear_estoque(
    *,
    usuario,
    estoque_id: int | None = None,
    fifo_nf: str = '',
    codigo_produto: str = '',
    motivo: str = '',
    observacao: str = '',
) -> int:
    qs = EstoqueFisico.objects.select_for_update().filter(
        status=EstoqueFisico.Status.ATIVO,
        quantidade__gt=0,
    )
    if estoque_id:
        qs = qs.filter(pk=estoque_id)
    else:
        fifo = (fifo_nf or '').strip()
        codigo = (codigo_produto or '').strip()
        if not fifo and not codigo:
            raise MovimentacaoError('Informe ID da linha, FIFO ou código do produto.')
        if fifo:
            qs = qs.filter(fifo_nf=fifo)
        if codigo:
            qs = qs.filter(codigo_produto=codigo)

    linhas = list(qs)
    if not linhas:
        raise MovimentacaoError('Nenhuma linha ativa encontrada para bloqueio.')

    count = 0
    for linha in linhas:
        linha.status = EstoqueFisico.Status.BLOQUEADO
        linha.save(update_fields=['status', 'updated_at'])
        _registrar_mov(
            tipo=MovimentacaoEstoque.Tipo.BLOQUEIO,
            linha=linha,
            codigo_produto=linha.codigo_produto,
            descricao=linha.descricao,
            quantidade=linha.quantidade,
            usuario=usuario,
            posicao_origem=linha.posicao,
            motivo=motivo,
            observacao=observacao,
        )
        count += 1
    return count


@transaction.atomic
def desbloquear_estoque(
    *,
    usuario,
    estoque_id: int | None = None,
    fifo_nf: str = '',
    codigo_produto: str = '',
    observacao: str = '',
) -> int:
    qs = EstoqueFisico.objects.select_for_update().filter(status=EstoqueFisico.Status.BLOQUEADO)
    if estoque_id:
        qs = qs.filter(pk=estoque_id)
    else:
        fifo = (fifo_nf or '').strip()
        codigo = (codigo_produto or '').strip()
        if fifo:
            qs = qs.filter(fifo_nf=fifo)
        if codigo:
            qs = qs.filter(codigo_produto=codigo)
    linhas = list(qs)
    if not linhas:
        raise MovimentacaoError('Nenhuma linha bloqueada encontrada.')

    count = 0
    for linha in linhas:
        linha.status = EstoqueFisico.Status.ATIVO
        linha.save(update_fields=['status', 'updated_at'])
        _registrar_mov(
            tipo=MovimentacaoEstoque.Tipo.DESBLOQUEIO,
            linha=linha,
            codigo_produto=linha.codigo_produto,
            descricao=linha.descricao,
            quantidade=linha.quantidade,
            usuario=usuario,
            posicao_origem=linha.posicao,
            observacao=observacao,
        )
        count += 1
    return count


def registrar_movimentacao_armazenagem(*, estoque: EstoqueFisico, usuario) -> MovimentacaoEstoque:
    return _registrar_mov(
        tipo=MovimentacaoEstoque.Tipo.ARMAZENAGEM,
        linha=estoque,
        codigo_produto=estoque.codigo_produto,
        descricao=estoque.descricao,
        quantidade=estoque.quantidade,
        usuario=usuario,
        posicao_destino=estoque.posicao,
        motivo=MovimentacaoEstoque.Motivo.OUTRO,
        observacao='Armazenagem TEMP',
    )
