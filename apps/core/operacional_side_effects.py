"""Efeitos colaterais pós-bipagem/finalização (fora do caminho crítico de resposta)."""

import logging
import sys
import threading
import time

from django.core.cache import cache
from django.db import transaction
from django.db import close_old_connections

logger = logging.getLogger(__name__)

BUFFER_CACHE_KEY = 'operacional:side-effects:buffer'
BUFFER_LOCK_CACHE_KEY = 'operacional:side-effects:buffer-lock'
BUFFER_PROCESSOR_CACHE_KEY = 'operacional:side-effects:processor-lock'
BUFFER_CACHE_TTL = 30
BUFFER_LOCK_TTL = 5
BUFFER_BATCH_SIZE = 128
BUFFER_DEBOUNCE_SECONDS = 0.05


def _side_effects_async_enabled():
    return not any(arg == 'test' for arg in sys.argv[1:])


def _buffer_lock_acquire(retries=10):
    for _ in range(retries):
        if cache.add(BUFFER_LOCK_CACHE_KEY, 1, BUFFER_LOCK_TTL):
            return True
        time.sleep(0.01)
    return False


def _buffer_lock_release():
    cache.delete(BUFFER_LOCK_CACHE_KEY)


def _buffer_get_events():
    return list(cache.get(BUFFER_CACHE_KEY, []) or [])


def _buffer_set_events(eventos):
    cache.set(BUFFER_CACHE_KEY, list(eventos), BUFFER_CACHE_TTL)


def _enqueue_event(evento):
    if not _buffer_lock_acquire():
        logger.warning('Falha ao adquirir lock do buffer operacional tipo=%s', evento.get('type'))
        return False
    try:
        eventos = _buffer_get_events()
        eventos.append(evento)
        _buffer_set_events(eventos)
        return True
    finally:
        _buffer_lock_release()


def _pop_batch_events(max_items=BUFFER_BATCH_SIZE):
    if not _buffer_lock_acquire():
        return []
    try:
        eventos = _buffer_get_events()
        if not eventos:
            return []
        lote = eventos[:max_items]
        restante = eventos[max_items:]
        if restante:
            _buffer_set_events(restante)
        else:
            cache.delete(BUFFER_CACHE_KEY)
        return lote
    finally:
        _buffer_lock_release()


def _schedule_buffer_processing():
    if not _side_effects_async_enabled():
        _process_buffer_until_empty()
        return
    if not cache.add(BUFFER_PROCESSOR_CACHE_KEY, 1, BUFFER_LOCK_TTL):
        return

    def _worker():
        try:
            time.sleep(BUFFER_DEBOUNCE_SECONDS)
            _process_buffer_until_empty()
        finally:
            cache.delete(BUFFER_PROCESSOR_CACHE_KEY)

    threading.Thread(target=_worker, name='operacional-side-effects', daemon=True).start()


def _registrar_evento_pos_commit(evento):
    def _registrar():
        if _enqueue_event(evento):
            _schedule_buffer_processing()

    transaction.on_commit(_registrar)


def _process_buffer_until_empty():
    close_old_connections()
    try:
        while True:
            lote = _pop_batch_events()
            if not lote:
                return
            inicio = time.perf_counter()
            _process_batch(lote)
            logger.info(
                'OPERACIONAL_BATCH_MS total_ms=%.2f eventos=%s',
                (time.perf_counter() - inicio) * 1000,
                len(lote),
            )
    finally:
        close_old_connections()


def _process_batch(eventos):
    invalidacao_operacional = False
    motivos_invalidacao = set()
    nf_ids_status = set()
    logs = []
    atividades = []
    conclusoes_automaticas = []
    finalizacoes_conferencia = []

    for evento in eventos:
        tipo = evento.get('type')
        payload = evento.get('payload', {})
        if tipo == 'invalidacao_operacional':
            invalidacao_operacional = True
            motivo = payload.get('motivo')
            if motivo:
                motivos_invalidacao.add(motivo)
        elif tipo == 'atualizar_status_nf':
            nf_id = payload.get('nf_id')
            if nf_id:
                nf_ids_status.add(nf_id)
        elif tipo == 'nf_ids_separacao':
            nf_ids_status.update(nf_id for nf_id in payload.get('nf_ids', []) if nf_id)
        elif tipo == 'log':
            logs.append(payload)
        elif tipo == 'atividade':
            atividades.append(payload)
        elif tipo == 'conclusao_automatica_separacao':
            conclusoes_automaticas.append(payload)
        elif tipo == 'finalizacao_conferencia':
            finalizacoes_conferencia.append(payload)

    _process_logs_batch(logs, atividades)
    _process_nf_status_batch(nf_ids_status)
    _process_conclusoes_automaticas_batch(conclusoes_automaticas)
    _process_finalizacoes_conferencia_batch(finalizacoes_conferencia)
    if invalidacao_operacional:
        _process_invalidacao_operacional_batch(','.join(sorted(motivos_invalidacao)))


def _process_logs_batch(logs, atividades):
    if not logs and not atividades:
        return
    try:
        from apps.logs.models import Log, UserActivityLog

        if logs:
            Log.objects.bulk_create([Log(**payload) for payload in logs], batch_size=100)
        if atividades:
            UserActivityLog.objects.bulk_create([UserActivityLog(**payload) for payload in atividades], batch_size=100)
    except Exception:
        logger.exception('Falha ao processar lote de logs operacionais')


def _process_nf_status_batch(nf_ids):
    if not nf_ids:
        return
    try:
        from apps.nf.models import NotaFiscal
        from apps.nf.services.status_service import sincronizar_status_operacional_nfs

        sincronizar_status_operacional_nfs(list(NotaFiscal.objects.filter(id__in=sorted(nf_ids))))
    except Exception:
        logger.exception('Falha ao sincronizar lote de status das NFs ids=%s', sorted(nf_ids))


def _process_conclusoes_automaticas_batch(eventos):
    if not eventos:
        return
    try:
        from apps.tarefas.models import Tarefa
        from apps.tarefas.services.separacao_service import _nfs_afetadas_tarefa, sincronizar_conclusao_automatica_tarefa
        from apps.nf.services.status_service import sincronizar_status_operacional_nfs

        nfs_afetadas = []
        for payload in eventos:
            tarefa = Tarefa.objects.filter(id=payload.get('tarefa_id')).first()
            if not tarefa:
                continue
            if sincronizar_conclusao_automatica_tarefa(tarefa, None):
                nfs_afetadas.extend(_nfs_afetadas_tarefa(tarefa))
        if nfs_afetadas:
            sincronizar_status_operacional_nfs(nfs_afetadas)
    except Exception:
        logger.exception('Falha ao processar lote de conclusao automatica da separacao')


def _process_invalidacao_operacional_batch(motivo=''):
    try:
        from apps.conferencia.services.conferencia_service import invalidate_nfs_disponiveis_cache
        from apps.core.services.visibilidade_operacional_service import invalidate_monitoramento_conferencia_cache
        from apps.core.views_dashboard import invalidate_dashboard_separacao_cache

        invalidate_nfs_disponiveis_cache(motivo=motivo)
        invalidate_dashboard_separacao_cache(motivo=motivo)
        invalidate_monitoramento_conferencia_cache(motivo=motivo)
    except Exception as exc:
        logger.warning('Falha ao invalidar cache operacional em lote motivo=%s erro=%s', motivo, exc)


def _executar_finalizacao_conferencia_payload(payload, *, fallback_flag=False):
    from django.utils import timezone

    from apps.conferencia.models import Conferencia
    from apps.conferencia.services.conferencia_service import _gerar_retorno_para_separacao, invalidate_nfs_disponiveis_cache
    from apps.core.services.visibilidade_operacional_service import invalidate_monitoramento_conferencia_cache
    from apps.core.views_dashboard import invalidate_dashboard_separacao_cache
    from apps.logs.models import Log, UserActivityLog
    from apps.nf.models import NotaFiscal
    from apps.nf.services.status_service import sincronizar_status_operacional_nf
    from apps.tarefas.models import Tarefa

    conferencia_id = payload['conferencia_id']
    nf_id = payload['nf_id']
    usuario_id = payload['usuario_id']
    possui_divergencia = payload['possui_divergencia']
    conferencia_liberada = payload['conferencia_liberada']
    detalhe_log = payload['detalhe_log']
    setor_cache = payload.get('setor_cache', '')

    logger.info(
        'CONFERENCIA_FINALIZACAO_SIDE_EFFECT_START conferencia_id=%s nf_id=%s fallback=%s',
        conferencia_id,
        nf_id,
        fallback_flag,
    )
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

    invalidate_nfs_disponiveis_cache(motivo='finalizacao_conferencia', nf_id=nf_id, setor=setor_cache)
    invalidate_dashboard_separacao_cache(motivo='finalizacao_conferencia')
    invalidate_monitoramento_conferencia_cache(motivo='finalizacao_conferencia', nf_id=nf_id, setor=setor_cache)
    logger.info(
        'CONFERENCIA_FINALIZACAO_SIDE_EFFECT_DONE conferencia_id=%s nf_id=%s fallback=%s',
        conferencia_id,
        nf_id,
        fallback_flag,
    )


def _process_finalizacoes_conferencia_batch(eventos):
    for payload in eventos:
        try:
            _executar_finalizacao_conferencia_payload(payload, fallback_flag=False)
        except Exception:
            logger.exception(
                'CONFERENCIA_FINALIZACAO_SIDE_EFFECT_ERROR conferencia_id=%s nf_id=%s fallback=%s',
                payload.get('conferencia_id'),
                payload.get('nf_id'),
                False,
            )


def agendar_invalidacao_operacional(*, motivo=''):
    """Invalida fila de conferência e dashboards somente após commit."""
    _registrar_evento_pos_commit({'type': 'invalidacao_operacional', 'payload': {'motivo': motivo}})


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
    _registrar_evento_pos_commit({'type': 'nf_ids_separacao', 'payload': {'nf_ids': nf_ids}})


def agendar_atualizar_status_nf(nf_id):
    if not nf_id:
        return
    _registrar_evento_pos_commit({'type': 'atualizar_status_nf', 'payload': {'nf_id': nf_id}})


def agendar_logs_bipagem_separacao(*, usuario_id, tarefa_id, produto_cod, finalizacao_automatica=False):
    from django.utils import timezone
    from apps.logs.models import UserActivityLog

    _registrar_evento_pos_commit(
        {
            'type': 'log',
            'payload': {
                'usuario_id': usuario_id,
                'acao': 'FINALIZACAO AUTOMATICA SEPARACAO' if finalizacao_automatica else 'BIPAGEM SEPARACAO',
                'detalhe': (
                    f'Tarefa {tarefa_id} finalizada automaticamente apos concluir a bipagem.'
                    if finalizacao_automatica
                    else f'Tarefa {tarefa_id} - produto {produto_cod} bipado.'
                ),
            },
        }
    )
    if not finalizacao_automatica:
        _registrar_evento_pos_commit(
            {
                'type': 'atividade',
                'payload': {
                    'usuario_id': usuario_id,
                    'tipo': UserActivityLog.Tipo.BIPAGEM,
                    'tarefa_id': tarefa_id,
                    'timestamp': timezone.now(),
                },
            }
        )


def agendar_logs_bipagem_conferencia(*, usuario_id, nf_numero, produto_cod, tarefa_id=None):
    from django.utils import timezone
    from apps.logs.models import UserActivityLog

    _registrar_evento_pos_commit(
        {
            'type': 'log',
            'payload': {
                'usuario_id': usuario_id,
                'acao': 'BIPAGEM CONFERENCIA',
                'detalhe': f'NF {nf_numero} - produto {produto_cod} bipado.',
            },
        }
    )
    _registrar_evento_pos_commit(
        {
            'type': 'atividade',
            'payload': {
                'usuario_id': usuario_id,
                'tipo': UserActivityLog.Tipo.BIPAGEM,
                'tarefa_id': tarefa_id,
                'timestamp': timezone.now(),
            },
        }
    )


def agendar_conclusao_automatica_separacao(*, tarefa_id, usuario_id):
    """Conclui tarefa e sincroniza NF fora do caminho crítico da última bipagem."""
    del usuario_id
    _registrar_evento_pos_commit({'type': 'conclusao_automatica_separacao', 'payload': {'tarefa_id': tarefa_id}})


def agendar_nf_ids_separacao(nf_ids):
    ids = [nf_id for nf_id in nf_ids if nf_id]
    if not ids:
        return
    _registrar_evento_pos_commit({'type': 'nf_ids_separacao', 'payload': {'nf_ids': ids}})


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
    _registrar_evento_pos_commit(
        {
            'type': 'finalizacao_conferencia',
            'payload': {
                'conferencia_id': conferencia_id,
                'nf_id': nf_id,
                'usuario_id': usuario_id,
                'possui_divergencia': possui_divergencia,
                'conferencia_liberada': conferencia_liberada,
                'detalhe_log': detalhe_log,
                'setor_cache': setor_cache,
            },
        }
    )
