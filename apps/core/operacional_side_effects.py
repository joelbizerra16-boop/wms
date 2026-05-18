"""Efeitos colaterais pós-bipagem/finalização (fora do caminho crítico de resposta)."""

import logging

from django.db import transaction

logger = logging.getLogger(__name__)


def agendar_invalidacao_operacional(*, motivo=''):
    """Invalida fila de conferência e dashboards somente após commit."""

    def _invalidar():
        try:
            from apps.conferencia.services.conferencia_service import invalidate_nfs_disponiveis_cache
            from apps.core.services.visibilidade_operacional_service import invalidate_monitoramento_conferencia_cache
            from apps.core.views_dashboard import invalidate_dashboard_separacao_cache

            invalidate_nfs_disponiveis_cache(motivo=motivo)
            invalidate_dashboard_separacao_cache(motivo=motivo)
            invalidate_monitoramento_conferencia_cache(motivo=motivo)
        except Exception as exc:
            logger.warning('Falha ao invalidar cache operacional motivo=%s erro=%s', motivo, exc)

    transaction.on_commit(_invalidar)


def agendar_sincronizar_nfs_separacao(nfs):
    """Atualiza status operacional das NFs após commit da bipagem."""

    nf_ids = []
    for nf in nfs:
        if nf is None:
            continue
        nf_id = getattr(nf, 'id', None)
        if nf_id:
            nf_ids.append(nf_id)
    if not nf_ids:
        return

    def _sincronizar():
        from apps.nf.models import NotaFiscal
        from apps.nf.services.status_service import sincronizar_status_operacional_nfs

        sincronizar_status_operacional_nfs(list(NotaFiscal.objects.filter(id__in=nf_ids)))

    transaction.on_commit(_sincronizar)


def agendar_atualizar_status_nf(nf_id):
    if not nf_id:
        return

    def _atualizar():
        from apps.nf.models import NotaFiscal
        from apps.nf.services.status_service import atualizar_status_nf

        nf = NotaFiscal.objects.filter(id=nf_id).first()
        if nf:
            atualizar_status_nf(nf)

    transaction.on_commit(_atualizar)


def agendar_logs_bipagem_separacao(*, usuario_id, tarefa_id, produto_cod, finalizacao_automatica=False):
    def _registrar():
        from apps.logs.models import Log, UserActivityLog
        from apps.tarefas.models import Tarefa
        from django.utils import timezone

        tarefa = Tarefa.objects.filter(id=tarefa_id).only('id').first()
        if not tarefa:
            return
        Log.objects.create(
            usuario_id=usuario_id,
            acao='FINALIZACAO AUTOMATICA SEPARACAO' if finalizacao_automatica else 'BIPAGEM SEPARACAO',
            detalhe=(
                f'Tarefa {tarefa_id} finalizada automaticamente apos concluir a bipagem.'
                if finalizacao_automatica
                else f'Tarefa {tarefa_id} - produto {produto_cod} bipado.'
            ),
        )
        if not finalizacao_automatica:
            UserActivityLog.objects.create(
                usuario_id=usuario_id,
                tipo=UserActivityLog.Tipo.BIPAGEM,
                tarefa_id=tarefa_id,
                timestamp=timezone.now(),
            )

    transaction.on_commit(_registrar)


def agendar_logs_bipagem_conferencia(*, usuario_id, nf_numero, produto_cod, tarefa_id=None):
    def _registrar():
        from apps.logs.models import Log, UserActivityLog
        from django.utils import timezone

        Log.objects.create(
            usuario_id=usuario_id,
            acao='BIPAGEM CONFERENCIA',
            detalhe=f'NF {nf_numero} - produto {produto_cod} bipado.',
        )
        UserActivityLog.objects.create(
            usuario_id=usuario_id,
            tipo=UserActivityLog.Tipo.BIPAGEM,
            tarefa_id=tarefa_id,
            timestamp=timezone.now(),
        )

    transaction.on_commit(_registrar)


def agendar_conclusao_automatica_separacao(*, tarefa_id, usuario_id):
    """Conclui tarefa e sincroniza NF fora do caminho crítico da última bipagem."""

    def _concluir():
        from apps.tarefas.models import Tarefa
        from apps.tarefas.services.separacao_service import _nfs_afetadas_tarefa, sincronizar_conclusao_automatica_tarefa

        tarefa = Tarefa.objects.filter(id=tarefa_id).first()
        if not tarefa:
            return
        if sincronizar_conclusao_automatica_tarefa(tarefa, None):
            from apps.nf.services.status_service import sincronizar_status_operacional_nfs

            sincronizar_status_operacional_nfs(_nfs_afetadas_tarefa(tarefa))

    transaction.on_commit(_concluir)


def agendar_nf_ids_separacao(nf_ids):
    ids = [nf_id for nf_id in nf_ids if nf_id]
    if not ids:
        return

    def _sincronizar():
        from apps.nf.models import NotaFiscal
        from apps.nf.services.status_service import sincronizar_status_operacional_nfs

        sincronizar_status_operacional_nfs(list(NotaFiscal.objects.filter(id__in=ids)))

    transaction.on_commit(_sincronizar)


def agendar_finalizacao_conferencia(
    *,
    conferencia_id,
    nf_id,
    usuario_id,
    possui_divergencia,
    conferencia_liberada,
    detalhe_log,
    setor_cache='',
):
    """Sync NF, logs, retorno separação e caches após commit da finalização."""

    def _executar():
        logger.info(
            'CONFERENCIA_FINALIZACAO_SIDE_EFFECT_START conferencia_id=%s nf_id=%s fallback=%s',
            conferencia_id,
            nf_id,
            False,
        )
        try:
            from django.utils import timezone

            from apps.conferencia.models import Conferencia
            from apps.conferencia.services.conferencia_service import _gerar_retorno_para_separacao
            from apps.core.services.visibilidade_operacional_service import invalidate_monitoramento_conferencia_cache
            from apps.core.views_dashboard import invalidate_dashboard_separacao_cache
            from apps.logs.models import Log, UserActivityLog
            from apps.nf.models import NotaFiscal
            from apps.nf.services.status_service import sincronizar_status_operacional_nf
            from apps.tarefas.models import Tarefa

            conferencia = Conferencia.objects.select_related('nf', 'nf__rota').prefetch_related('itens__produto').get(id=conferencia_id)
            nf = NotaFiscal.objects.filter(id=nf_id).first() or conferencia.nf
            if nf:
                sincronizar_status_operacional_nf(nf)

            if possui_divergencia and not conferencia_liberada:
                _gerar_retorno_para_separacao(conferencia)

            Log.objects.create(usuario_id=usuario_id, acao='FINALIZACAO CONFERENCIA', detalhe=detalhe_log)
            tarefa_id = Tarefa.objects.filter(nf_id=nf_id).values_list('id', flat=True).first()
            UserActivityLog.objects.create(
                usuario_id=usuario_id,
                tipo=UserActivityLog.Tipo.TAREFA_FIM,
                tarefa_id=tarefa_id,
                timestamp=timezone.now(),
            )

            from apps.conferencia.services.conferencia_service import invalidate_nfs_disponiveis_cache

            invalidate_nfs_disponiveis_cache(motivo='finalizacao_conferencia', nf_id=nf_id, setor=setor_cache)
            invalidate_dashboard_separacao_cache(motivo='finalizacao_conferencia')
            invalidate_monitoramento_conferencia_cache(motivo='finalizacao_conferencia', nf_id=nf_id, setor=setor_cache)
            logger.info(
                'CONFERENCIA_FINALIZACAO_SIDE_EFFECT_DONE conferencia_id=%s nf_id=%s fallback=%s',
                conferencia_id,
                nf_id,
                False,
            )
        except Exception:
            logger.exception(
                'CONFERENCIA_FINALIZACAO_SIDE_EFFECT_ERROR conferencia_id=%s nf_id=%s fallback=%s',
                conferencia_id,
                nf_id,
                False,
            )

    transaction.on_commit(_executar)
