from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect

from apps.core.services.liberacao_divergencia_service import (
    LiberacaoDivergenciaError,
    liberar_nf_divergencia,
    liberar_tarefa_divergencia,
)
from apps.logs.models import LiberacaoDivergencia
from apps.conferencia.models import Conferencia
from apps.nf.models import NotaFiscal
from apps.core.nf_utils import resolve_nf_numero
from apps.tarefas.models import Tarefa
from apps.usuarios.access import require_profiles
from apps.usuarios.models import Usuario


def _redirect_back(request, fallback):
    next_url = (request.POST.get('next') or '').strip()
    if next_url.startswith('/'):
        return redirect(next_url)
    return redirect(fallback)


def _usuario_pode_excluir(usuario):
    if not usuario.is_authenticated:
        return False
    if usuario.is_superuser:
        return True
    if getattr(usuario, 'perfil', None) == Usuario.Perfil.GESTOR:
        return True
    return usuario.groups.filter(name='GESTAO').exists()


def _nf_da_tarefa(tarefa):
    if tarefa.nf_id:
        return tarefa.nf
    item_nf = tarefa.itens.select_related('nf').filter(nf__isnull=False).order_by('id').first()
    return item_nf.nf if item_nf else None


def _conferencia_finalizada(nf):
    if nf.status in {NotaFiscal.Status.CONCLUIDO, NotaFiscal.Status.CONCLUIDO_COM_RESTRICAO}:
        return True
    ultima = nf.conferencias.exclude(status=Conferencia.Status.CANCELADA).order_by('-created_at').first()
    if ultima is None:
        return False
    return ultima.status in {Conferencia.Status.OK, Conferencia.Status.CONCLUIDO_COM_RESTRICAO}


@require_profiles(Usuario.Perfil.GESTOR)
def liberar_tarefa_divergencia_view(request, tarefa_id):
    if request.method != 'POST':
        return redirect('web-separacao-exec', tarefa_id=tarefa_id)

    tarefa = get_object_or_404(Tarefa.objects.select_related('nf'), id=tarefa_id)
    try:
        liberar_tarefa_divergencia(
            tarefa=tarefa,
            usuario=request.user,
            senha=request.POST.get('senha'),
            motivo=request.POST.get('motivo'),
        )
        messages.success(request, f'Tarefa {tarefa.id} liberada com restricao e registrada para auditoria.')
    except LiberacaoDivergenciaError as exc:
        messages.error(request, str(exc))

    return _redirect_back(request, f'/separacao/{tarefa.id}/')


@require_profiles(Usuario.Perfil.GESTOR)
def liberar_nf_divergencia_view(request, nf_id):
    if request.method != 'POST':
        return redirect('web-conferencia-lista')

    nf = get_object_or_404(
        NotaFiscal.objects.prefetch_related('conferencias').filter(ativa=True).exclude(status_fiscal=NotaFiscal.StatusFiscal.CANCELADA),
        id=nf_id,
    )
    try:
        liberar_nf_divergencia(
            nf=nf,
            usuario=request.user,
            senha=request.POST.get('senha'),
            motivo=request.POST.get('motivo'),
        )
        messages.success(request, f'NF {nf.numero} liberada com restricao e registrada para auditoria.')
    except LiberacaoDivergenciaError as exc:
        messages.error(request, str(exc))

    return _redirect_back(request, '/conferencia/')


def excluir_tarefa_view(request, tarefa_id):
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido.'}, status=405)
    if not _usuario_pode_excluir(request.user):
        return JsonResponse({'erro': 'Sem permissão para excluir tarefa.'}, status=403)

    motivo = (request.POST.get('motivo') or '').strip()
    if not motivo:
        return JsonResponse({'success': False, 'error': 'Informe o motivo da exclusão.'}, status=400)

    tarefa = get_object_or_404(Tarefa.objects.select_related('nf'), id=tarefa_id, ativo=True)
    status_anterior = tarefa.status
    if status_anterior in {Tarefa.Status.CONCLUIDO, Tarefa.Status.CONCLUIDO_COM_RESTRICAO}:
        return JsonResponse({'erro': 'Tarefa finalizada não pode ser excluída.'}, status=400)

    tarefa.ativo = False
    tarefa.usuario = None
    tarefa.save(update_fields=['ativo', 'usuario', 'updated_at'])

    nf_vinculada = _nf_da_tarefa(tarefa)

    LiberacaoDivergencia.objects.create(
        usuario=request.user,
        tarefa=tarefa,
        nf=nf_vinculada,
        motivo=f'[EXCLUSAO] {motivo}',
        nf_numero=resolve_nf_numero(obj={'nf_numero': nf_vinculada.numero}) if nf_vinculada else None,
        status_anterior=status_anterior,
        status_novo='EXCLUIDO',
    )
    return JsonResponse({'success': True, 'message': 'Tarefa excluída com sucesso.', 'sucesso': True})


def excluir_nf_conferencia_view(request, nf_id):
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido.'}, status=405)
    if not _usuario_pode_excluir(request.user):
        return JsonResponse({'erro': 'Sem permissão para excluir conferência.'}, status=403)

    motivo = (request.POST.get('motivo') or '').strip()
    if not motivo:
        return JsonResponse({'success': False, 'error': 'Informe o motivo da exclusão.'}, status=400)

    nf = get_object_or_404(NotaFiscal.objects.filter(ativa=True), id=nf_id)
    status_anterior = nf.status
    if _conferencia_finalizada(nf):
        return JsonResponse({'erro': 'Conferência finalizada não pode ser excluída.'}, status=400)

    nf.ativa = False
    nf.save(update_fields=['ativa', 'updated_at'])

    LiberacaoDivergencia.objects.create(
        usuario=request.user,
        nf=nf,
        motivo=f'[EXCLUSAO] {motivo}',
        nf_numero=resolve_nf_numero(obj={'nf_numero': nf.numero}),
        status_anterior=status_anterior,
        status_novo='EXCLUIDO',
    )
    return JsonResponse({'success': True, 'message': 'Conferência excluída com sucesso.', 'sucesso': True})