"""Fallback resiliente quando o módulo de ondas não está disponível no schema."""

from __future__ import annotations

import logging

from django.db import transaction

from apps.tarefas.models import Tarefa
from apps.usuarios.models import Setor

logger = logging.getLogger(__name__)


def obter_ou_criar_tarefa_classica(*, nf, rota, setor, tarefas_lote_cache=None):
    if setor == Setor.Codigo.FILTROS:
        tarefa, _criada = Tarefa.objects.get_or_create(
            nf=nf,
            tipo=Tarefa.Tipo.FILTRO,
            setor=Setor.Codigo.FILTROS,
            rota=rota,
            defaults={'status': Tarefa.Status.ABERTO},
        )
        return tarefa

    chave_lote = ('fallback_classico', setor, rota.id)
    if tarefas_lote_cache is not None and chave_lote in tarefas_lote_cache:
        return tarefas_lote_cache[chave_lote]

    tarefa = (
        Tarefa.objects.filter(
            nf__isnull=True,
            tipo=Tarefa.Tipo.ROTA,
            setor=setor,
            rota=rota,
            status=Tarefa.Status.ABERTO,
        )
        .order_by('-id')
        .first()
    )
    if tarefa is None:
        tarefa = Tarefa.objects.create(
            nf=None,
            tipo=Tarefa.Tipo.ROTA,
            setor=setor,
            rota=rota,
            status=Tarefa.Status.ABERTO,
        )
    if tarefas_lote_cache is not None:
        tarefas_lote_cache[chave_lote] = tarefa
    return tarefa


def obter_tarefa_separacao_com_fallback_onda(
    *,
    nf,
    rota,
    setor,
    tipo_embalagem,
    tarefas_lote_cache=None,
):
    """
    Tenta fluxo de onda em savepoint isolado; em falha (ex.: tabela ausente),
    recupera a transação pai e usa separação clássica.
    """
    from apps.tarefas.services.onda_service import obter_ou_criar_tarefa_onda

    try:
        with transaction.atomic():
            return obter_ou_criar_tarefa_onda(
                nf=nf,
                rota=rota,
                setor=setor,
                tipo_embalagem=tipo_embalagem,
                tarefas_lote_cache=tarefas_lote_cache,
            )
    except Exception as exc:
        transaction.set_rollback(False)
        logger.exception(
            'ONDA_FALLBACK_TRANSACIONAL nf_id=%s nf_numero=%s rota_id=%s rota_nome=%s '
            'setor=%s exception=%s transaction_recuperada=True',
            getattr(nf, 'id', None),
            getattr(nf, 'numero', ''),
            getattr(rota, 'id', None),
            getattr(rota, 'nome', ''),
            setor,
            exc,
        )
        tarefa = obter_ou_criar_tarefa_classica(
            nf=nf,
            rota=rota,
            setor=setor,
            tarefas_lote_cache=tarefas_lote_cache,
        )
        return tarefa, None
