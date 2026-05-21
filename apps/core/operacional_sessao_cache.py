"""Mapa de bipagem em cache por sessão operacional (zero query redundante por leitura)."""

from __future__ import annotations

import logging

from django.core.cache import cache

from apps.core.bipagem_leitura import codigo_bipagem_primario, variantes_codigo_barras

logger = logging.getLogger(__name__)

SESSAO_BIPAGEM_TTL = 300


def _chave_separacao(tarefa_id):
    return f'wms:bip:map:sep:{tarefa_id}'


def _chave_conferencia(conferencia_id):
    return f'wms:bip:map:conf:{conferencia_id}'


def _montar_mapa_itens_pendentes(itens_pendentes):
    mapa = {}
    for item in itens_pendentes:
        produto = getattr(item, 'produto', None)
        if produto is None:
            continue
        for campo in ('cod_ean', 'cod_prod', 'codigo'):
            valor = getattr(produto, campo, None) or ''
            for variante in variantes_codigo_barras(valor):
                mapa[variante] = item.id
    return mapa


def preload_mapa_bipagem_separacao(tarefa_id, *, itens_pendentes=None):
    if itens_pendentes is None:
        from decimal import Decimal

        from django.db.models import F

        from apps.tarefas.models import TarefaItem

        itens_pendentes = list(
            TarefaItem.objects.filter(
                tarefa_id=tarefa_id,
                quantidade_separada__lt=F('quantidade_total'),
            )
            .select_related('produto')
            .only(
                'id',
                'produto__id',
                'produto__cod_ean',
                'produto__cod_prod',
                'produto__codigo',
            )
        )
    mapa = _montar_mapa_itens_pendentes(itens_pendentes)
    cache.set(_chave_separacao(tarefa_id), mapa, SESSAO_BIPAGEM_TTL)
    logger.info('CACHE_MISS modulo=separacao entidade_id=%s itens_mapa=%s origem=preload', tarefa_id, len(mapa))
    return mapa


def preload_mapa_bipagem_conferencia(conferencia_id, *, itens_pendentes=None):
    if itens_pendentes is None:
        from django.db.models import F

        from apps.conferencia.models import ConferenciaItem

        itens_pendentes = list(
            ConferenciaItem.objects.filter(
                conferencia_id=conferencia_id,
                status='AGUARDANDO',
                qtd_conferida__lt=F('qtd_esperada'),
            )
            .select_related('produto')
            .only(
                'id',
                'produto__id',
                'produto__cod_ean',
                'produto__cod_prod',
                'produto__codigo',
            )
        )
    mapa = _montar_mapa_itens_pendentes(itens_pendentes)
    cache.set(_chave_conferencia(conferencia_id), mapa, SESSAO_BIPAGEM_TTL)
    logger.info('CACHE_MISS modulo=conferencia entidade_id=%s itens_mapa=%s origem=preload', conferencia_id, len(mapa))
    return mapa


def resolver_item_id_separacao(tarefa_id, codigo, *, itens_pendentes=None):
    mapa = cache.get(_chave_separacao(tarefa_id))
    if mapa is None:
        mapa = preload_mapa_bipagem_separacao(tarefa_id, itens_pendentes=itens_pendentes)
        cache_hit = False
    else:
        cache_hit = True
    for variante in variantes_codigo_barras(codigo):
        item_id = mapa.get(variante)
        if item_id:
            logger.info(
                'CACHE_HIT modulo=separacao entidade_id=%s item_id=%s variante=%s',
                tarefa_id,
                item_id,
                variante,
            )
            return item_id, cache_hit
    logger.info('CACHE_MISS modulo=separacao entidade_id=%s codigo=%s', tarefa_id, codigo_bipagem_primario(codigo, modulo='separacao'))
    return None, cache_hit


def resolver_item_id_conferencia(conferencia_id, codigo, *, itens_pendentes=None):
    mapa = cache.get(_chave_conferencia(conferencia_id))
    if mapa is None:
        mapa = preload_mapa_bipagem_conferencia(conferencia_id, itens_pendentes=itens_pendentes)
        cache_hit = False
    else:
        cache_hit = True
    for variante in variantes_codigo_barras(codigo):
        item_id = mapa.get(variante)
        if item_id:
            logger.info(
                'CACHE_HIT modulo=conferencia entidade_id=%s item_id=%s variante=%s',
                conferencia_id,
                item_id,
                variante,
            )
            return item_id, cache_hit
    logger.info('CACHE_MISS modulo=conferencia entidade_id=%s codigo=%s', conferencia_id, codigo_bipagem_primario(codigo, modulo='conferencia'))
    return None, cache_hit


def invalidar_mapa_separacao(tarefa_id):
    cache.delete(_chave_separacao(tarefa_id))


def invalidar_mapa_conferencia(conferencia_id):
    cache.delete(_chave_conferencia(conferencia_id))


def atualizar_mapa_apos_bipagem_separacao(tarefa_id, item_id, codigo):
    mapa = cache.get(_chave_separacao(tarefa_id)) or {}
    for variante in variantes_codigo_barras(codigo):
        mapa[variante] = item_id
    cache.set(_chave_separacao(tarefa_id), mapa, SESSAO_BIPAGEM_TTL)
