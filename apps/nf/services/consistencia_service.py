from django.db.models import F, Q

from apps.conferencia.models import Conferencia
from apps.logs.models import Log
from apps.nf.models import NotaFiscal
from apps.tarefas.models import Tarefa, TarefaItem


def _conferencias_validas_nf(nf):
    conferencias = getattr(nf, '_prefetched_objects_cache', {}).get('conferencias')
    if conferencias is not None:
        return [conferencia for conferencia in conferencias if conferencia.status != Conferencia.Status.CANCELADA]
    return nf.conferencias.exclude(status=Conferencia.Status.CANCELADA)


def separacao_concluida_nf(nf):
    tarefas_nf = (
        Tarefa.objects.filter(Q(nf=nf) | Q(itens__nf=nf))
        .prefetch_related('itens')
        .distinct()
    )
    if not tarefas_nf.exists():
        return False

    itens_nf = (
        TarefaItem.objects.filter(Q(tarefa__nf=nf) | Q(nf=nf))
        .select_related('tarefa')
        .distinct()
    )
    if not itens_nf.exists():
        return False

    return not itens_nf.filter(quantidade_separada__lt=F('quantidade_total')).exists()


def sanear_consistencia_nf(nf, *, actor=None, persist=False, exigir_conferencia=False):
    conferencias_validas = _conferencias_validas_nf(nf)
    separacao_ok = separacao_concluida_nf(nf)

    if not separacao_ok:
        removidas = len(conferencias_validas) if isinstance(conferencias_validas, list) else conferencias_validas.count()
        if persist and removidas:
            nf.conferencias.exclude(status=Conferencia.Status.CANCELADA).delete()
        if persist and nf.status != NotaFiscal.Status.INCONSISTENTE:
            nf.status = NotaFiscal.Status.INCONSISTENTE
            nf.bloqueada = True
            nf.save(update_fields=['status', 'bloqueada', 'updated_at'])
        if removidas and actor is not None:
            Log.objects.create(
                usuario=actor,
                acao='SANEAMENTO FLUXO NF',
                detalhe=f'NF {nf.numero}: conferencias removidas por separacao nao concluida ({removidas}).',
            )
        return {
            'valida': False,
            'motivo': 'separacao_nao_concluida',
            'conferencias_removidas': removidas,
        }

    if exigir_conferencia and not (bool(conferencias_validas) if isinstance(conferencias_validas, list) else conferencias_validas.exists()):
        if persist and nf.status != NotaFiscal.Status.INCONSISTENTE:
            nf.status = NotaFiscal.Status.INCONSISTENTE
            nf.bloqueada = True
            nf.save(update_fields=['status', 'bloqueada', 'updated_at'])
        if actor is not None:
            Log.objects.create(
                usuario=actor,
                acao='SANEAMENTO FLUXO NF',
                detalhe=f'NF {nf.numero}: sem conferencia vinculada apos separacao concluida.',
            )
        return {'valida': False, 'motivo': 'sem_conferencia'}

    return {'valida': True, 'motivo': ''}


def sanear_consistencia_fluxo():
    total = 0
    inconsistentes = 0
    for nf in NotaFiscal.objects.prefetch_related('conferencias').all():
        total += 1
        resultado = sanear_consistencia_nf(nf, persist=True, exigir_conferencia=True)
        if not resultado['valida']:
            inconsistentes += 1
    return {'total': total, 'inconsistentes': inconsistentes}
