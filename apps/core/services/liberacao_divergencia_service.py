from django.contrib.auth import authenticate
from django.db import transaction

from apps.conferencia.models import Conferencia
from apps.logs.models import LiberacaoDivergencia, Log
from apps.nf.models import NotaFiscal
from apps.nf.services.status_service import sincronizar_status_operacional_nf, sincronizar_status_operacional_nfs
from apps.tarefas.models import Tarefa
from apps.usuarios.models import Usuario


class LiberacaoDivergenciaError(Exception):
    pass


def liberar_tarefa_divergencia(*, tarefa, usuario, senha, motivo):
    _validar_gestor(usuario)
    _validar_senha(usuario, senha)
    motivo_validado = _validar_motivo(motivo)
    if tarefa.nf_id is None:
        raise LiberacaoDivergenciaError('Tarefa sem NF vinculada nao pode ser liberada.')

    if tarefa.status not in {Tarefa.Status.FECHADO_COM_RESTRICAO, Tarefa.Status.LIBERADO_COM_RESTRICAO}:
        raise LiberacaoDivergenciaError('Tarefa nao esta com restricao liberavel.')

    status_anterior = tarefa.status
    with transaction.atomic():
        tarefa.status = Tarefa.Status.LIBERADO_COM_RESTRICAO
        tarefa.save(update_fields=['status', 'updated_at'])
        LiberacaoDivergencia.objects.create(
            usuario=usuario,
            nf=tarefa.nf,
            tarefa=tarefa,
            motivo=motivo_validado,
            nf_numero=tarefa.nf.numero,
            status_anterior=status_anterior,
            status_novo=Tarefa.Status.LIBERADO_COM_RESTRICAO,
        )
        Log.objects.create(
            usuario=usuario,
            acao='LIBERACAO DIVERGENCIA',
            detalhe=f'Tarefa {tarefa.id} liberada com restricao. Motivo: {motivo_validado}.',
        )
        sincronizar_status_operacional_nfs([tarefa.nf, *[item.nf for item in tarefa.itens.select_related('nf').all() if item.nf_id]])

    return tarefa


def liberar_nf_divergencia(*, nf, usuario, senha, motivo):
    _validar_gestor(usuario)
    _validar_senha(usuario, senha)
    motivo_validado = _validar_motivo(motivo)

    conferencia = nf.conferencias.exclude(status=Conferencia.Status.CANCELADA).order_by('-created_at').first()
    if conferencia is None or conferencia.status not in {Conferencia.Status.DIVERGENCIA, Conferencia.Status.LIBERADO_COM_RESTRICAO}:
        raise LiberacaoDivergenciaError('NF nao possui divergencia liberavel.')

    status_anterior = conferencia.status
    with transaction.atomic():
        conferencia.status = Conferencia.Status.LIBERADO_COM_RESTRICAO
        conferencia.save(update_fields=['status', 'updated_at'])
        LiberacaoDivergencia.objects.create(
            usuario=usuario,
            nf=nf,
            tarefa=None,
            motivo=motivo_validado,
            nf_numero=nf.numero,
            status_anterior=status_anterior,
            status_novo=Conferencia.Status.LIBERADO_COM_RESTRICAO,
        )
        Log.objects.create(
            usuario=usuario,
            acao='LIBERACAO DIVERGENCIA',
            detalhe=f'NF {nf.numero} liberada com restricao. Motivo: {motivo_validado}.',
        )
        sincronizar_status_operacional_nf(nf)

    return conferencia


def _validar_gestor(usuario):
    if usuario.perfil != Usuario.Perfil.GESTOR:
        raise LiberacaoDivergenciaError('Somente usuarios de gestao podem liberar divergencia.')


def _validar_senha(usuario, senha):
    autenticado = authenticate(username=usuario.username, password=(senha or '').strip())
    if autenticado is None:
        raise LiberacaoDivergenciaError('Senha invalida para liberacao de divergencia.')


def _validar_motivo(motivo):
    motivo_validado = (motivo or '').strip()
    if not motivo_validado:
        raise LiberacaoDivergenciaError('Motivo da liberacao e obrigatorio.')
    return motivo_validado