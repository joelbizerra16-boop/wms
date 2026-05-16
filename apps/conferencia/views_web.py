from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from apps.conferencia.models import Conferencia, ConferenciaItem
from apps.conferencia.services.conferencia_service import (
    ConferenciaError,
    avaliar_liberacao_conferencia,
    bipar_conferencia,
    finalizar_conferencia,
    listar_nfs_disponiveis,
    registrar_divergencia,
)
from apps.logs.models import Log
from apps.nf.models import NotaFiscal
from apps.nf.services.status_service import atualizar_status_nf
from apps.usuarios.access import build_access_context, require_profiles
from apps.usuarios.models import Usuario
from apps.usuarios.session_utils import usuario_esta_logado

FEEDBACK_SESSION_KEY = 'conferencia_feedback'


def _render(request, template_name, context=None):
    base_context = {'usuario': request.user}
    base_context.update(build_access_context(request.user))
    if context:
        base_context.update(context)
    return render(request, template_name, base_context)


def _pagination_query(request):
    params = request.GET.copy()
    params.pop('page', None)
    query = params.urlencode()
    return f'&{query}' if query else ''


def _paginar_lista(request, itens, por_pagina=None):
    if por_pagina is None:
        from django.conf import settings
        por_pagina = int(getattr(settings, 'OPERATIONAL_PAGE_SIZE', 50))
    paginador = Paginator(itens, por_pagina)
    page_obj = paginador.get_page(request.GET.get('page'))
    return {
        'page_obj': page_obj,
        'is_paginated': page_obj.has_other_pages(),
        'pagination_query': _pagination_query(request),
    }


def _obter_conferencia_contexto(nf_id, usuario):
    nf = get_object_or_404(
        NotaFiscal.objects.select_related('cliente', 'rota').defer('bairro').prefetch_related(
            'itens__produto',
            Prefetch('conferencias', queryset=Conferencia.objects.select_related('conferente').prefetch_related('itens__produto')),
        ),
        id=nf_id,
    )
    _validar_acesso_nf_por_setor(nf, usuario)
    atualizar_status_nf(nf)
    conferencia_em_uso = nf.conferencias.filter(status=Conferencia.Status.EM_CONFERENCIA).select_related('conferente').first()
    if conferencia_em_uso and not usuario_esta_logado(conferencia_em_uso.conferente):
        conferencia_em_uso.status = Conferencia.Status.AGUARDANDO
        conferencia_em_uso.save(update_fields=['status', 'updated_at'])
        conferencia_em_uso = None
    conferencia_ativa = nf.conferencias.filter(
        status__in=[Conferencia.Status.EM_CONFERENCIA, Conferencia.Status.LIBERADO_COM_RESTRICAO],
        conferente=usuario,
    ).first()
    conferencia_recente = conferencia_ativa or nf.conferencias.exclude(status=Conferencia.Status.CANCELADA).order_by('-created_at').first()
    return nf, conferencia_recente, conferencia_ativa, conferencia_em_uso


def _item_atual(conferencia):
    if conferencia is None:
        return None
    itens_relacao = getattr(conferencia, 'itens', None)
    itens = list(itens_relacao.select_related('produto').all()) if itens_relacao else []
    for item in itens:
        if item.status == ConferenciaItem.Status.AGUARDANDO:
            return item
    for item in itens:
        if item.status == ConferenciaItem.Status.DIVERGENCIA:
            return item
    return itens[0] if itens else None


def _totais_nf(conferencia):
    if conferencia is None:
        return {'esperado': 0, 'conferido': 0, 'pendentes': 0, 'divergencias': 0}
    itens_relacao = getattr(conferencia, 'itens', None)
    itens = list(itens_relacao.all()) if itens_relacao else []
    return {
        'esperado': sum(item.qtd_esperada for item in itens),
        'conferido': sum(item.qtd_conferida for item in itens),
        'pendentes': sum(1 for item in itens if item.status == ConferenciaItem.Status.AGUARDANDO),
        'divergencias': sum(1 for item in itens if item.status == ConferenciaItem.Status.DIVERGENCIA),
    }


def _pop_feedback(request):
    return request.session.pop(FEEDBACK_SESSION_KEY, None)


def _set_feedback(request, *, tipo, mensagem, atual=None):
    request.session[FEEDBACK_SESSION_KEY] = {
        'tipo': tipo,
        'mensagem': mensagem,
        'atual': atual,
    }


def _criar_itens_conferencia(conferencia):
    itens_relacao = getattr(conferencia, 'itens', None)
    if itens_relacao and itens_relacao.exists():
        return
    for item_nf in conferencia.nf.itens.select_related('produto').all():
        ConferenciaItem.objects.create(
            conferencia=conferencia,
            produto=item_nf.produto,
            qtd_esperada=item_nf.quantidade,
            qtd_conferida=0,
            status=ConferenciaItem.Status.AGUARDANDO,
        )


def _registrar_tentativa_bloqueada(usuario, nf, motivo):
    Log.objects.create(
        usuario=usuario,
        acao='ACESSO CONFERENCIA BLOQUEADO',
        detalhe=f'Tentativa bloqueada para NF {nf.numero}. Motivo: {motivo}.',
    )


def _conferencia_finalizada(conferencia):
    if conferencia is None:
        return False
    return conferencia.status in {Conferencia.Status.OK, Conferencia.Status.CONCLUIDO_COM_RESTRICAO}


def _setores_nf(nf):
    itens_relacao = getattr(nf, 'itens', None)
    itens = itens_relacao.select_related('produto').all() if itens_relacao else []
    setores = set()
    for item_nf in itens:
        categoria = ((getattr(getattr(item_nf, 'produto', None), 'categoria', None) or '').strip().upper())
        if categoria == 'FILTRO':
            categoria = 'FILTROS'
        if categoria:
            setores.add(categoria)
    return setores


def _validar_acesso_nf_por_setor(nf, usuario):
    if getattr(usuario, 'is_superuser', False):
        return
    setores_nf = _setores_nf(nf)
    if not setores_nf:
        return
    if not usuario.setores.filter(nome__in=setores_nf).exists():
        raise PermissionDenied('Usuário sem acesso ao setor')


@transaction.atomic
def _aceitar_conferencia(nf, usuario):
    validacao_fluxo = avaliar_liberacao_conferencia(nf)
    if not validacao_fluxo['liberado']:
        raise ConferenciaError(validacao_fluxo['motivo'] or 'Pedido ainda não foi separado')

    conferencia_em_uso = nf.conferencias.filter(status=Conferencia.Status.EM_CONFERENCIA).select_related('conferente').first()
    if conferencia_em_uso and conferencia_em_uso.conferente_id != usuario.id:
        raise ConferenciaError(
            f'Conferência em uso por: {conferencia_em_uso.conferente.nome or conferencia_em_uso.conferente.username}'
        )
    conferencia = (
        nf.conferencias.filter(
            status__in=[Conferencia.Status.EM_CONFERENCIA, Conferencia.Status.AGUARDANDO, Conferencia.Status.LIBERADO_COM_RESTRICAO]
        )
        .select_related('conferente')
        .order_by('-created_at')
        .first()
    )
    conferencia_final = (
        nf.conferencias.exclude(status=Conferencia.Status.CANCELADA)
        .order_by('-created_at')
        .first()
    )
    if _conferencia_finalizada(conferencia_final):
        raise ConferenciaError('Conferência já finalizada para esta NF.')
    if conferencia is None:
        conferencia = Conferencia.objects.create(nf=nf, conferente=usuario, status=Conferencia.Status.EM_CONFERENCIA)
    else:
        conferencia.conferente = usuario
        if conferencia.status == Conferencia.Status.AGUARDANDO:
            conferencia.status = Conferencia.Status.EM_CONFERENCIA
        conferencia.save(update_fields=['conferente', 'status', 'updated_at'])
    _criar_itens_conferencia(conferencia)
    return conferencia


@require_profiles(Usuario.Perfil.CONFERENTE, Usuario.Perfil.GESTOR)
def conferencia_lista_web(request):
    paginacao = _paginar_lista(request, listar_nfs_disponiveis(request.user), por_pagina=50)
    contexto = {
        'nfs': paginacao['page_obj'],
        **paginacao,
    }
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return _render(request, 'partials/conferencia_lista_tabela.html', contexto)
    return _render(request, 'conferencia_lista.html', contexto)


@require_profiles(Usuario.Perfil.CONFERENTE, Usuario.Perfil.GESTOR)
def aceitar_conferencia_web(request, nf_id):
    if request.method != 'POST':
        return redirect('web-conferencia-exec', nf_id=nf_id)
    try:
        nf = get_object_or_404(
            NotaFiscal.objects.select_related('cliente', 'rota').defer('bairro').prefetch_related('itens__produto', 'conferencias'),
            id=nf_id,
        )
        _validar_acesso_nf_por_setor(nf, request.user)
    except PermissionDenied as exc:
        return HttpResponseForbidden(str(exc))
    conferencia_recente = nf.conferencias.exclude(status=Conferencia.Status.CANCELADA).order_by('-created_at').first()
    if _conferencia_finalizada(conferencia_recente):
        return HttpResponseForbidden('Conferência já finalizada.')
    validacao_fluxo = avaliar_liberacao_conferencia(nf)
    if not validacao_fluxo['liberado']:
        _registrar_tentativa_bloqueada(request.user, nf, validacao_fluxo['motivo'])
        return HttpResponseForbidden(validacao_fluxo['motivo'])

    try:
        _aceitar_conferencia(nf, request.user)
        messages.success(request, 'Conferência aceita e iniciada.')
    except ConferenciaError as exc:
        messages.error(request, str(exc))
    return redirect('web-conferencia-exec', nf_id=nf_id)


@require_profiles(Usuario.Perfil.CONFERENTE, Usuario.Perfil.GESTOR)
def conferir_nf(request, nf_id):
    try:
        nf, conferencia, conferencia_ativa, conferencia_em_uso = _obter_conferencia_contexto(nf_id, request.user)
    except PermissionDenied as exc:
        return HttpResponseForbidden(str(exc))

    conferencia_ja_finalizada = _conferencia_finalizada(conferencia)
    validacao_fluxo = avaliar_liberacao_conferencia(nf)
    if conferencia_em_uso and conferencia_em_uso.conferente_id != request.user.id:
        messages.error(
            request,
            f'Conferência em uso por: {conferencia_em_uso.conferente.nome or conferencia_em_uso.conferente.username}',
        )
        return redirect('web-conferencia-lista')

    if not validacao_fluxo['liberado']:
        _registrar_tentativa_bloqueada(request.user, nf, validacao_fluxo['motivo'])
        if request.method == 'POST':
            return HttpResponseForbidden(validacao_fluxo['motivo'])

    if request.method == 'POST':
        if conferencia_ja_finalizada:
            return HttpResponseForbidden('Conferência já finalizada.')
        acao = request.POST.get('acao')
        try:
            if acao == 'iniciar':
                _aceitar_conferencia(nf, request.user)
                messages.success(request, 'Conferência iniciada.')
            elif acao == 'bipar':
                codigo = (request.POST.get('codigo') or '').strip()
                if not codigo:
                    raise ConferenciaError('Informe o código para bipagem.')
                conferencia_em_andamento = conferencia_ativa or conferencia
                if not conferencia_em_andamento:
                    raise ConferenciaError('Inicie a conferência antes de bipar itens.')
                resultado = bipar_conferencia(conferencia_em_andamento.id, codigo, request.user)
                _set_feedback(request, tipo='ok', mensagem='Leitura OK', atual=resultado['conferido'])
            elif acao == 'finalizar_restricao':
                if not conferencia_ativa:
                    raise ConferenciaError('Nenhuma conferência em andamento para tratar.')
                item = _item_atual(conferencia_ativa)
                if item and item.status == ConferenciaItem.Status.AGUARDANDO and conferencia_ativa.status != Conferencia.Status.LIBERADO_COM_RESTRICAO:
                    return redirect('web-conferencia-divergencia', item_id=item.id)
                finalizar_conferencia(conferencia_ativa.id, request.user)
                messages.success(request, 'Conferência finalizada.')
            elif acao == 'finalizar':
                if not conferencia_ativa:
                    raise ConferenciaError('Nenhuma conferência em andamento para finalizar.')
                finalizar_conferencia(conferencia_ativa.id, request.user)
                messages.success(request, 'Conferência finalizada.')
            elif acao == 'continuar_depois':
                if conferencia_ativa and conferencia_ativa.status == Conferencia.Status.EM_CONFERENCIA:
                    conferencia_ativa.status = Conferencia.Status.AGUARDANDO
                    conferencia_ativa.save(update_fields=['status', 'updated_at'])
                messages.warning(request, 'Conferência pausada. NF liberada para nova entrada.')
                return redirect('web-conferencia-lista')
        except ConferenciaError as exc:
            _set_feedback(request, tipo='erro', mensagem=str(exc))
        return redirect('web-conferencia-exec', nf_id=nf.id)

    try:
        nf, conferencia, conferencia_ativa, conferencia_em_uso = _obter_conferencia_contexto(nf_id, request.user)
    except PermissionDenied as exc:
        return HttpResponseForbidden(str(exc))
    item = _item_atual(conferencia_ativa or conferencia)
    return _render(
        request,
        'conferencia_exec.html',
        {
            'nf': nf,
            'conferencia': conferencia,
            'conferencia_ativa': conferencia_ativa,
            'item': item,
            'totais_nf': _totais_nf(conferencia_ativa or conferencia),
            'feedback': _pop_feedback(request),
            'conferencia_liberada': validacao_fluxo['liberado'],
            'conferencia_bloqueio_motivo': validacao_fluxo['motivo'],
            'conferencia_finalizada': conferencia_ja_finalizada,
        },
    )


@require_profiles(Usuario.Perfil.CONFERENTE, Usuario.Perfil.GESTOR)
def registrar_divergencia_web(request, item_id):
    item = get_object_or_404(
        ConferenciaItem.objects.select_related('conferencia__nf', 'produto', 'conferencia__conferente'),
        id=item_id,
    )
    conferencia = item.conferencia
    if conferencia.conferente_id != request.user.id:
        raise Http404('Item de divergência não disponível para o usuário.')

    if request.method == 'POST':
        motivo = request.POST.get('motivo')
        observacao = request.POST.get('observacao')
        try:
            registrar_divergencia(item.id, motivo, observacao, request.user)
            messages.success(request, 'Divergência registrada.')
            return redirect('web-conferencia-exec', nf_id=conferencia.nf_id)
        except ConferenciaError as exc:
            messages.error(request, str(exc))

    return _render(
        request,
        'divergencia.html',
        {
            'item': item,
            'motivos': [
                (ConferenciaItem.MotivoDivergencia.FALTA, 'Falta'),
                (ConferenciaItem.MotivoDivergencia.EXCESSO, 'Sobra'),
                (ConferenciaItem.MotivoDivergencia.PRODUTO_ERRADO, 'Produto errado'),
            ],
        },
    )