from decimal import Decimal

from apps.conferencia.models import Conferencia
from apps.nf.models import NotaFiscal
from apps.tarefas.models import Tarefa, TarefaItem


def _ultima_conferencia(nf):
    return nf.conferencias.exclude(status=Conferencia.Status.CANCELADA).order_by('-created_at').first()


def _status_base_nf(nf, conferencias_validas):
    itens_nf = [item for item in nf.itens.all() if item.produto_id is not None]
    if not itens_nf or not conferencias_validas:
        return NotaFiscal.Status.PENDENTE

    conferencia_itens = {}
    for conferencia in conferencias_validas:
        for item in conferencia.itens.all():
            conferencia_itens[item.produto_id] = item
    total = sum((item.quantidade for item in itens_nf), Decimal('0'))
    conferido = Decimal('0')

    for item_nf in itens_nf:
        conferencia_item = conferencia_itens.get(item_nf.produto_id)
        if conferencia_item is None:
            continue
        conferido += min(conferencia_item.qtd_conferida, item_nf.quantidade)

    if total > 0 and conferido >= total:
        return NotaFiscal.Status.CONCLUIDO
    if conferido > 0:
        return NotaFiscal.Status.EM_CONFERENCIA
    return NotaFiscal.Status.PENDENTE


def atualizar_status_nf(nf):
    if nf is None:
        return None

    itens_com_restricao = list(
        TarefaItem.objects.select_related('tarefa')
        .filter(nf=nf, possui_restricao=True)
    )
    conferencias_validas = list(nf.conferencias.exclude(status=Conferencia.Status.CANCELADA).prefetch_related('itens'))
    ultima_conferencia = _ultima_conferencia(nf)
    status_base = _status_base_nf(nf, conferencias_validas)

    possui_bloqueio_tarefa = any(item.tarefa.status == Tarefa.Status.FECHADO_COM_RESTRICAO for item in itens_com_restricao)
    possui_liberacao_tarefa = any(
        item.tarefa.status in {Tarefa.Status.LIBERADO_COM_RESTRICAO, Tarefa.Status.CONCLUIDO_COM_RESTRICAO}
        for item in itens_com_restricao
    )
    possui_bloqueio_conferencia = any(
        conferencia.status == Conferencia.Status.DIVERGENCIA for conferencia in conferencias_validas
    )
    possui_liberacao_conferencia = any(
        conferencia.status in {Conferencia.Status.LIBERADO_COM_RESTRICAO, Conferencia.Status.CONCLUIDO_COM_RESTRICAO}
        for conferencia in conferencias_validas
    )

    if possui_liberacao_conferencia:
        status = NotaFiscal.Status.CONCLUIDO_COM_RESTRICAO if status_base == NotaFiscal.Status.CONCLUIDO else NotaFiscal.Status.LIBERADA_COM_RESTRICAO
    elif possui_bloqueio_tarefa or possui_bloqueio_conferencia:
        status = NotaFiscal.Status.BLOQUEADA_COM_RESTRICAO
    elif possui_liberacao_tarefa:
        status = NotaFiscal.Status.CONCLUIDO_COM_RESTRICAO if status_base == NotaFiscal.Status.CONCLUIDO else NotaFiscal.Status.LIBERADA_COM_RESTRICAO
    else:
        status = status_base

    bloqueada = status == NotaFiscal.Status.BLOQUEADA_COM_RESTRICAO or nf.status_fiscal == NotaFiscal.StatusFiscal.CANCELADA
    campos = []
    if nf.status != status:
        nf.status = status
        campos.append('status')
    if nf.bloqueada != bloqueada:
        nf.bloqueada = bloqueada
        campos.append('bloqueada')
    if campos:
        campos.append('updated_at')
        nf.save(update_fields=campos)
    return nf


def sincronizar_status_operacional_nf(nf):
    return atualizar_status_nf(nf)


def sincronizar_status_operacional_nfs(nfs):
    ids_processados = set()
    for nf in nfs:
        if nf is None or nf.id in ids_processados:
            continue
        sincronizar_status_operacional_nf(nf)
        ids_processados.add(nf.id)