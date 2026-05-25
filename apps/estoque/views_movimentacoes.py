import logging

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import redirect
from django.urls import reverse

from apps.estoque.db_schema import aplicar_schema_estoque_brownfield, tabelas_estoque_existem
from apps.estoque.models import EstoqueFisico, MovimentacaoEstoque
from apps.estoque.services.auditoria import coletar_indicadores_auditoria
from apps.estoque.services.movimentacao import (
    MovimentacaoError,
    ajustar_estoque,
    bloquear_estoque,
    desbloquear_estoque,
    reabastecer_estoque,
    transferir_estoque,
)
from apps.estoque.views_web import MSG_SCHEMA_PENDENTE, PAGE_SIZE, _garantir_schema_estoque, _render
from apps.usuarios.access import require_profiles
from apps.usuarios.models import Usuario
logger = logging.getLogger(__name__)

ABAS = (
    ('transferencia', 'Transferência'),
    ('reabastecimento', 'Reabastecimento'),
    ('ajustes', 'Ajustes'),
    ('bloqueios', 'Bloqueios'),
    ('auditoria', 'Auditoria'),
    ('historico', 'Histórico'),
)

MOTIVOS_AJUSTE = [
    MovimentacaoEstoque.Motivo.INVENTARIO,
    MovimentacaoEstoque.Motivo.AVARIA,
    MovimentacaoEstoque.Motivo.QUEBRA,
    MovimentacaoEstoque.Motivo.SOBRA,
    MovimentacaoEstoque.Motivo.DIVERGENCIA,
    MovimentacaoEstoque.Motivo.ERRO_OPERACIONAL,
]

MOTIVOS_BLOQUEIO = [
    MovimentacaoEstoque.Motivo.AVARIA,
    MovimentacaoEstoque.Motivo.QUARENTENA,
    MovimentacaoEstoque.Motivo.DIVERGENCIA,
    MovimentacaoEstoque.Motivo.QUALIDADE,
    MovimentacaoEstoque.Motivo.RECALL,
    MovimentacaoEstoque.Motivo.INVENTARIO,
]


@require_profiles(Usuario.Perfil.GESTOR)
def estoque_movimentacoes_web(request):
    if not _garantir_schema_estoque():
        messages.error(request, MSG_SCHEMA_PENDENTE)
        return _render(request, 'estoque/schema_pendente.html', {'comando': 'migrate estoque --noinput'})

    aba = request.GET.get('aba') or request.POST.get('aba') or 'transferencia'
    if aba not in {a[0] for a in ABAS}:
        aba = 'transferencia'

    if request.method == 'POST':
        acao = request.POST.get('acao', '')
        try:
            if acao == 'transferir':
                transferir_estoque(
                    codigo_produto=request.POST.get('codigo_produto'),
                    posicao_origem=request.POST.get('posicao_origem'),
                    posicao_destino=request.POST.get('posicao_destino'),
                    quantidade=request.POST.get('quantidade'),
                    usuario=request.user,
                    fifo_nf=request.POST.get('fifo_nf', ''),
                    observacao=request.POST.get('observacao', ''),
                )
                messages.success(request, 'Transferência confirmada. FIFO preservado.')
            elif acao == 'reabastecer':
                reabastecer_estoque(
                    codigo_produto=request.POST.get('codigo_produto'),
                    posicao_origem=request.POST.get('posicao_origem'),
                    posicao_destino=request.POST.get('posicao_destino'),
                    quantidade=request.POST.get('quantidade'),
                    usuario=request.user,
                    observacao=request.POST.get('observacao', ''),
                )
                messages.success(request, 'Reabastecimento pulmão → picking confirmado.')
            elif acao == 'ajustar':
                positivo = request.POST.get('direcao_ajuste') != 'negativo'
                ajustar_estoque(
                    codigo_produto=request.POST.get('codigo_produto'),
                    posicao_entrada=request.POST.get('posicao'),
                    quantidade=request.POST.get('quantidade'),
                    usuario=request.user,
                    motivo=request.POST.get('motivo') or MovimentacaoEstoque.Motivo.INVENTARIO,
                    observacao=request.POST.get('observacao', ''),
                    positivo=positivo,
                )
                messages.success(request, 'Ajuste registrado no histórico.')
            elif acao == 'bloquear':
                n = bloquear_estoque(
                    usuario=request.user,
                    estoque_id=int(request.POST['estoque_id']) if request.POST.get('estoque_id') else None,
                    fifo_nf=request.POST.get('fifo_nf', ''),
                    codigo_produto=request.POST.get('codigo_produto', ''),
                    motivo=request.POST.get('motivo') or MovimentacaoEstoque.Motivo.QUARENTENA,
                    observacao=request.POST.get('observacao', ''),
                )
                messages.success(request, f'{n} linha(s) bloqueada(s).')
            elif acao == 'desbloquear':
                n = desbloquear_estoque(
                    usuario=request.user,
                    estoque_id=int(request.POST['estoque_id']) if request.POST.get('estoque_id') else None,
                    fifo_nf=request.POST.get('fifo_nf', ''),
                    codigo_produto=request.POST.get('codigo_produto', ''),
                    observacao=request.POST.get('observacao', ''),
                )
                messages.success(request, f'{n} linha(s) desbloqueada(s).')
            else:
                messages.warning(request, 'Ação não reconhecida.')
        except (MovimentacaoError, ValueError) as exc:
            messages.error(request, str(exc))
        except Exception as exc:
            logger.exception('MOVIMENTACAO_WEB_ERRO acao=%s', acao)
            messages.error(request, str(exc))

        return redirect(f'{reverse("web-estoque-movimentacoes")}?aba={aba}')

    bloqueados = (
        EstoqueFisico.objects.filter(status=EstoqueFisico.Status.BLOQUEADO, quantidade__gt=0)
        .select_related('posicao')[:50]
    )

    historico_qs = MovimentacaoEstoque.objects.select_related(
        'usuario', 'posicao_origem', 'posicao_destino'
    ).order_by('-created_at')
    busca_hist = (request.GET.get('busca_hist') or '').strip()
    if busca_hist:
        historico_qs = historico_qs.filter(codigo_produto__icontains=busca_hist)
    paginator = Paginator(historico_qs, PAGE_SIZE)
    page_hist = paginator.get_page(request.GET.get('page'))

    historico_rows = []
    for mov in page_hist.object_list:
        historico_rows.append(
            {
                'data': mov.created_at,
                'usuario': getattr(mov.usuario, 'nome', mov.usuario_id),
                'tipo': mov.get_tipo_display(),
                'produto': mov.codigo_produto,
                'origem': mov.posicao_origem.label_coletor if mov.posicao_origem else '-',
                'destino': mov.posicao_destino.label_coletor if mov.posicao_destino else '-',
                'qtd': mov.quantidade,
                'fifo': mov.fifo_nf or '-',
                'nf': mov.nf_entrada or '-',
                'status': mov.get_status_display(),
            }
        )

    return _render(
        request,
        'estoque/movimentacoes.html',
        {
            'aba': aba,
            'abas': ABAS,
            'motivos_ajuste': MOTIVOS_AJUSTE,
            'motivos_bloqueio': MOTIVOS_BLOQUEIO,
            'auditoria_itens': coletar_indicadores_auditoria(),
            'bloqueados': bloqueados,
            'page_hist': page_hist,
            'historico': historico_rows,
            'is_paginated_hist': page_hist.has_other_pages(),
            'busca_hist': busca_hist,
            'pagination_query_hist': '&aba=historico' + (f'&busca_hist={busca_hist}' if busca_hist else ''),
        },
    )
