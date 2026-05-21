"""Fallback resiliente quando o módulo de ondas não está disponível no schema."""

from __future__ import annotations

import logging

from django.db import connection, transaction
from django.utils import timezone

from apps.tarefas.models import Tarefa
from apps.usuarios.models import Setor

logger = logging.getLogger(__name__)


def _carregar_tarefa_legado(pk: int) -> Tarefa:
    return queryset_tarefa_legado().get(pk=pk)


def _criar_tarefa_legado(*, tipo: str, setor: str, nf_id, rota_id: int, status: str) -> Tarefa:
    agora = timezone.now()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO tarefas_tarefa (
                created_at, updated_at, tipo, setor, nf_id, rota_id, status, ativo
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            [agora, agora, tipo, setor, nf_id, rota_id, status, True],
        )
        pk = cursor.fetchone()[0]
    return _carregar_tarefa_legado(pk)


def obter_ou_criar_tarefa_classica(*, nf, rota, setor, tarefas_lote_cache=None):
    from apps.tarefas.services.onda_schema import (
        coluna_tarefa_onda_id_disponivel,
        queryset_tarefa_legado,
    )

    usar_query_legado = not coluna_tarefa_onda_id_disponivel()
    qs = queryset_tarefa_legado() if usar_query_legado else Tarefa.objects

    if setor == Setor.Codigo.FILTROS:
        if usar_query_legado:
            tarefa = qs.filter(
                nf_id=nf.id,
                tipo=Tarefa.Tipo.FILTRO,
                setor=Setor.Codigo.FILTROS,
                rota_id=rota.id,
            ).first()
            if tarefa is None:
                tarefa = _criar_tarefa_legado(
                    tipo=Tarefa.Tipo.FILTRO,
                    setor=Setor.Codigo.FILTROS,
                    nf_id=nf.id,
                    rota_id=rota.id,
                    status=Tarefa.Status.ABERTO,
                )
            return tarefa

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
        qs.filter(
            nf__isnull=True,
            tipo=Tarefa.Tipo.ROTA,
            setor=setor,
            rota_id=rota.id,
            status=Tarefa.Status.ABERTO,
        )
        .order_by('-id')
        .first()
    )
    if tarefa is None:
        if usar_query_legado:
            tarefa = _criar_tarefa_legado(
                tipo=Tarefa.Tipo.ROTA,
                setor=setor,
                nf_id=None,
                rota_id=rota.id,
                status=Tarefa.Status.ABERTO,
            )
        else:
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
    Tenta fluxo de onda em savepoint isolado quando o schema suporta.
    Caso contrário (ou em falha), usa separação clássica sem tocar colunas de onda.
    """
    from apps.tarefas.services.onda_schema import schema_onda_disponivel

    if not schema_onda_disponivel():
        tarefa = obter_ou_criar_tarefa_classica(
            nf=nf,
            rota=rota,
            setor=setor,
            tarefas_lote_cache=tarefas_lote_cache,
        )
        return tarefa, None

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
        invalidate = False
        try:
            from apps.tarefas.services.onda_schema import invalidate_schema_onda_cache

            invalidate_schema_onda_cache()
            invalidate = True
        except Exception:
            pass
        logger.exception(
            'ONDA_FALLBACK_TRANSACIONAL nf_id=%s nf_numero=%s rota_id=%s rota_nome=%s '
            'setor=%s exception=%s transaction_recuperada=True schema_cache_invalidado=%s',
            getattr(nf, 'id', None),
            getattr(nf, 'numero', ''),
            getattr(rota, 'id', None),
            getattr(rota, 'nome', ''),
            setor,
            exc,
            invalidate,
        )
        tarefa = obter_ou_criar_tarefa_classica(
            nf=nf,
            rota=rota,
            setor=setor,
            tarefas_lote_cache=tarefas_lote_cache,
        )
        return tarefa, None
