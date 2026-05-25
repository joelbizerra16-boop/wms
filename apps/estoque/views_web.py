import logging

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Q
from django.db.utils import ProgrammingError
from django.shortcuts import get_object_or_404, redirect, render

from apps.estoque.db_schema import aplicar_schema_estoque_brownfield, tabelas_estoque_existem
from apps.estoque.models import EstoqueFisico, PosicaoEstoque
from apps.estoque.services.armazenagem import ArmazenagemError, armazenar_item_temp
from apps.estoque.services.posicao import montar_codigo_posicao
from apps.recebimento.models import EstoqueTemporario
from apps.usuarios.access import build_access_context, require_profiles
from apps.usuarios.models import Usuario

logger = logging.getLogger(__name__)

PAGE_SIZE = 50

MSG_SCHEMA_PENDENTE = (
    'Tabelas do estoque ainda não existem no banco. '
    'No servidor, execute: python manage.py migrate estoque --noinput'
)


def _garantir_schema_estoque():
    if tabelas_estoque_existem(connection):
        return True
    return aplicar_schema_estoque_brownfield(connection)


def _paginar(qs, request, page_size=PAGE_SIZE):
    paginator = Paginator(qs, page_size)
    page_obj = paginator.get_page(request.GET.get('page'))
    try:
        total = paginator.count
    except ProgrammingError:
        total = len(page_obj.object_list)
    return page_obj, total


def _render(request, template_name, context=None):
    base_context = {'usuario': request.user}
    base_context.update(build_access_context(request.user))
    if context:
        base_context.update(context)
    return render(request, template_name, base_context)


@require_profiles(Usuario.Perfil.GESTOR)
def estoque_posicoes_web(request):
    if not _garantir_schema_estoque():
        messages.error(request, MSG_SCHEMA_PENDENTE)
        return _render(request, 'estoque/schema_pendente.html', {'comando': 'migrate estoque --noinput'})

    if request.method == 'POST':
        acao = request.POST.get('acao', 'criar')
        rua = (request.POST.get('rua') or '').strip()
        posicao = (request.POST.get('posicao') or '').strip()
        andar = (request.POST.get('andar') or '').strip()
        lado = (request.POST.get('lado') or '').strip()
        setor = (request.POST.get('setor') or '').strip()
        status = request.POST.get('status') or PosicaoEstoque.Status.ATIVA
        observacao = (request.POST.get('observacao') or '').strip()
        codigo = (request.POST.get('codigo_posicao') or '').strip() or montar_codigo_posicao(
            rua=rua, posicao=posicao, andar=andar, lado=lado
        )

        if not all([rua, posicao, andar, lado, codigo]):
            messages.error(request, 'Preencha rua, posição, andar, lado e código.')
        elif acao == 'editar':
            pos = get_object_or_404(PosicaoEstoque, pk=request.POST.get('posicao_id'))
            if PosicaoEstoque.objects.exclude(pk=pos.pk).filter(codigo_posicao__iexact=codigo).exists():
                messages.error(request, f'Código {codigo} já existe.')
            else:
                pos.codigo_posicao = codigo
                pos.rua, pos.posicao, pos.andar, pos.lado = rua, posicao, andar, lado
                pos.setor = setor
                pos.status = status
                pos.observacao = observacao
                pos.save()
                messages.success(request, f'Posição {codigo} atualizada.')
        elif PosicaoEstoque.objects.filter(codigo_posicao__iexact=codigo).exists():
            messages.error(request, f'Código {codigo} já cadastrado.')
        else:
            PosicaoEstoque.objects.create(
                codigo_posicao=codigo,
                rua=rua,
                posicao=posicao,
                andar=andar,
                lado=lado,
                setor=setor,
                status=status,
                observacao=observacao,
            )
            messages.success(request, f'Posição {codigo} cadastrada.')
        return redirect('web-estoque-posicoes')

    qs = PosicaoEstoque.objects.filter(ativo=True).order_by('rua', 'posicao', 'andar', 'lado')
    busca = (request.GET.get('busca') or '').strip()
    if busca:
        qs = qs.filter(
            Q(codigo_posicao__icontains=busca)
            | Q(rua__icontains=busca)
            | Q(posicao__icontains=busca)
            | Q(setor__icontains=busca)
        )
    try:
        page_obj, total_posicoes = _paginar(qs, request)
    except ProgrammingError as exc:
        logger.exception('ESTOQUE_POSICOES_QUERY_ERRO: %s', exc)
        messages.error(request, MSG_SCHEMA_PENDENTE)
        return _render(request, 'estoque/schema_pendente.html', {'comando': 'migrate estoque --noinput'})

    return _render(
        request,
        'estoque/posicoes.html',
        {
            'page_obj': page_obj,
            'posicoes': page_obj.object_list,
            'is_paginated': page_obj.has_other_pages(),
            'pagination_query': f'busca={busca}' if busca else '',
            'busca': busca,
            'status_choices': PosicaoEstoque.Status.choices,
            'total_posicoes': total_posicoes,
        },
    )


@require_profiles(Usuario.Perfil.GESTOR)
def estoque_lista_web(request):
    if not _garantir_schema_estoque():
        messages.error(request, MSG_SCHEMA_PENDENTE)
        return _render(request, 'estoque/schema_pendente.html', {'comando': 'migrate estoque --noinput'})

    qs = (
        EstoqueFisico.objects.filter(status=EstoqueFisico.Status.ATIVO)
        .select_related('posicao', 'produto')
        .order_by('data_entrada', 'codigo_produto')
    )
    busca = (request.GET.get('busca') or '').strip()
    if busca:
        qs = qs.filter(
            Q(codigo_produto__icontains=busca)
            | Q(descricao__icontains=busca)
            | Q(fifo_nf__icontains=busca)
            | Q(nf_entrada__icontains=busca)
            | Q(posicao__codigo_posicao__icontains=busca)
        )
    try:
        page_obj, total_itens = _paginar(qs, request)
    except ProgrammingError as exc:
        logger.exception('ESTOQUE_LISTA_QUERY_ERRO: %s', exc)
        messages.error(request, MSG_SCHEMA_PENDENTE)
        return _render(request, 'estoque/schema_pendente.html', {'comando': 'migrate estoque --noinput'})

    itens = []
    for row in page_obj.object_list:
        itens.append(
            {
                'id': row.id,
                'codigo_posicao': row.posicao.codigo_posicao,
                'rua': row.posicao.rua,
                'label_posicao': row.posicao.label_coletor,
                'produto': row.codigo_produto,
                'descricao': row.descricao,
                'quantidade': row.quantidade,
                'fifo': row.fifo_nf,
                'dias': row.dias_em_estoque,
                'apta_separacao': row.posicao.apta_para_separacao(),
            }
        )
    return _render(
        request,
        'estoque/estoque_lista.html',
        {
            'page_obj': page_obj,
            'itens': itens,
            'is_paginated': page_obj.has_other_pages(),
            'pagination_query': f'busca={busca}' if busca else '',
            'busca': busca,
            'total_itens': total_itens,
        },
    )


@require_profiles(Usuario.Perfil.GESTOR)
def estoque_armazenagem_web(request):
    if not _garantir_schema_estoque() and request.method != 'GET':
        messages.error(request, MSG_SCHEMA_PENDENTE)
        return redirect('web-estoque-armazenagem')

    temp_id = request.GET.get('temp') or request.POST.get('temp_id')
    if request.method == 'POST':
        posicao_entrada = (request.POST.get('posicao') or '').strip()
        try:
            estoque = armazenar_item_temp(
                temp_id=int(request.POST.get('temp_id')),
                posicao_entrada=posicao_entrada,
                usuario=request.user,
            )
            messages.success(
                request,
                f'Armazenado: {estoque.codigo_produto} → {estoque.posicao.label_coletor} (FIFO {estoque.fifo_nf}).',
            )
            return redirect('web-estoque-armazenagem')
        except (ArmazenagemError, ValueError) as exc:
            messages.error(request, str(exc))

    itens_temp = (
        EstoqueTemporario.objects.filter(status=EstoqueTemporario.Status.TEMP)
        .select_related('usuario_recebimento')
        .order_by('data_recebimento', 'nf_numero')[:100]
    )
    item_selecionado = None
    if temp_id:
        item_selecionado = EstoqueTemporario.objects.filter(
            pk=temp_id, status=EstoqueTemporario.Status.TEMP
        ).first()
    return _render(
        request,
        'estoque/armazenagem.html',
        {
            'itens_temp': itens_temp,
            'item_selecionado': item_selecionado,
            'total_temp': EstoqueTemporario.objects.filter(status=EstoqueTemporario.Status.TEMP).count(),
        },
    )


@require_profiles(Usuario.Perfil.GESTOR)
def estoque_movimentacoes_web(request):
    return _render(request, 'estoque/movimentacoes.html')
