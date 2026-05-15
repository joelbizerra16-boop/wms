from datetime import date
from decimal import Decimal
import logging

from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db.models import F, Prefetch, Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.conferencia.models import Conferencia, ConferenciaItem
from apps.core.services.visibilidade_operacional_service import (
    get_nfs_monitoramento_conferencia,
    get_tarefas_para_separacao,
)
from apps.core.nf_utils import resolve_nf_numero
from apps.logs.models import LiberacaoDivergencia
from apps.nf.models import NotaFiscal, NotaFiscalItem
from apps.nf.services.consistencia_service import separacao_concluida_nf
from apps.nf.services.status_service import atualizar_status_nf
from apps.tarefas.models import Tarefa, TarefaItem
from apps.usuarios.access import build_access_context, require_profiles
from apps.usuarios.models import Setor, Usuario

logger = logging.getLogger(__name__)


STATUS_TAREFA_DASHBOARD_SEPARACAO = {
    Tarefa.Status.ABERTO,
    Tarefa.Status.EM_EXECUCAO,
    Tarefa.Status.CONCLUIDO,
    Tarefa.Status.FECHADO_COM_RESTRICAO,
    Tarefa.Status.LIBERADO_COM_RESTRICAO,
    Tarefa.Status.CONCLUIDO_COM_RESTRICAO,
}


def _render(request, template_name, context=None):
    base_context = {'usuario': request.user}
    base_context.update(build_access_context(request.user))
    if context:
        base_context.update(context)
    return render(request, template_name, base_context)


def _build_ocultar_detalhe_url(request):
    params = request.GET.copy()
    params.pop('nf_detalhe', None)
    qs = params.urlencode()
    return f'{request.path}?{qs}' if qs else request.path


def _pagination_query(request):
    params = request.GET.copy()
    params.pop('page', None)
    params.pop('partial', None)
    query = params.urlencode()
    return f'&{query}' if query else ''


def _paginar_lista(request, itens, por_pagina=20):
    paginador = Paginator(itens, por_pagina)
    page_obj = paginador.get_page(request.GET.get('page'))
    return {
        'page_obj': page_obj,
        'is_paginated': page_obj.has_other_pages(),
        'pagination_query': _pagination_query(request),
    }


def _build_detalhe_nf_context(request, nf_numero):
    nf_numero = ''.join(str(nf_numero or '').split())
    if not nf_numero:
        return None
    nf = (
        NotaFiscal.objects.select_related('cliente', 'rota')
        .filter(numero=nf_numero, ativa=True)
        .first()
    )
    if nf is None:
        return None

    separacao_itens = list(
        TarefaItem.objects.select_related('produto', 'bipado_por', 'tarefa')
        .filter(data_bipagem__isnull=False)
        .filter(Q(nf=nf) | Q(tarefa__nf=nf))
        .order_by('-data_bipagem', '-updated_at')
    )
    separacao_historico = [
        {
            'data_bipagem': item.data_bipagem,
            'usuario': (item.bipado_por.nome or item.bipado_por.username) if item.bipado_por_id else '-',
            'produto': item.produto.cod_prod,
            'descricao': item.produto.descricao,
            'quantidade': float(item.quantidade_separada),
        }
        for item in separacao_itens
    ]

    separacao_por_produto = {}
    for item in separacao_itens:
        # Mantem o registro mais recente por produto para rastreabilidade cruzada.
        if item.produto_id not in separacao_por_produto:
            separacao_por_produto[item.produto_id] = item
    conferencia_itens = (
        ConferenciaItem.objects.select_related('produto', 'bipado_por', 'conferencia', 'conferencia__conferente')
        .filter(conferencia__nf=nf)
        .filter(Q(data_bipagem__isnull=False) | Q(qtd_conferida__gt=0))
        .order_by('-data_bipagem', '-updated_at')
    )
    conferencia_rastreabilidade = []
    for item in conferencia_itens:
        separacao_item = separacao_por_produto.get(item.produto_id)
        conferencia_rastreabilidade.append(
            {
                'data_conferencia': item.data_bipagem,
                'usuario_conferencia': (item.bipado_por.nome or item.bipado_por.username) if item.bipado_por_id else (
                    item.conferencia.conferente.nome or item.conferencia.conferente.username
                ),
                'data_separacao': separacao_item.data_bipagem if separacao_item else None,
                'usuario_separacao': (
                    (separacao_item.bipado_por.nome or separacao_item.bipado_por.username)
                    if separacao_item and separacao_item.bipado_por_id
                    else '-'
                ),
                'produto': item.produto.cod_prod,
                'descricao': item.produto.descricao,
                'quantidade': float(item.qtd_conferida or item.qtd_esperada),
            }
        )

    # Se ainda nao houve bipagem de conferencia, mostra ao menos o historico real da separacao.
    if not conferencia_rastreabilidade and separacao_historico:
        conferencia_rastreabilidade = [
            {
                'data_conferencia': None,
                'usuario_conferencia': '-',
                'data_separacao': row['data_bipagem'],
                'usuario_separacao': row['usuario'],
                'produto': row['produto'],
                'descricao': row['descricao'],
                'quantidade': row['quantidade'],
            }
            for row in separacao_historico
        ]

    return {
        'numero': nf.numero,
        'cliente': nf.cliente.nome,
        'rota': nf.rota.nome,
        'separacao_historico': separacao_historico,
        'conferencia_rastreabilidade': conferencia_rastreabilidade,
        'ocultar_url': _build_ocultar_detalhe_url(request),
    }


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _resolver_periodo_e_busca(request, *, default_today=False):
    date_from_raw = (request.GET.get('date_from') or request.GET.get('data_inicial') or '').strip()
    date_to_raw = (request.GET.get('date_to') or request.GET.get('data_final') or '').strip()
    logger.debug('dashboard_separacao data_inicial=%s', request.GET.get('data_inicial') or request.GET.get('date_from'))
    logger.debug('dashboard_separacao data_final=%s', request.GET.get('data_final') or request.GET.get('date_to'))
    date_from = _parse_date(date_from_raw)
    date_to = _parse_date(date_to_raw)
    if default_today and date_from is None and date_to is None:
        hoje = timezone.localdate()
        date_from = hoje
        date_to = hoje
    if date_from and date_to and date_to < date_from:
        date_to = date_from
    busca = (request.GET.get('busca') or request.GET.get('q') or '').strip().lower()
    return date_from, date_to, busca


def _normalizar_setor_dashboard(valor):
    setor = (valor or '').strip().upper()
    if setor == 'FILTRO':
        return Setor.Codigo.FILTROS
    if setor == 'NAO ENCONTRADO':
        return Setor.Codigo.NAO_ENCONTRADO
    return setor


def _setores_usuario_dashboard(usuario):
    if usuario is None:
        return set()
    setores = list(usuario.setores.values_list('nome', flat=True))
    if not setores and getattr(usuario, 'setor', None) and usuario.setor != Setor.Codigo.NAO_ENCONTRADO:
        setores = [usuario.setor]
    return {_normalizar_setor_dashboard(valor) for valor in setores if _normalizar_setor_dashboard(valor)}


def _tarefas_base_dashboard_separacao(usuario):
    tarefas = list(
        Tarefa.objects.select_related('nf', 'nf__cliente', 'rota')
        .defer('nf__bairro')
        .prefetch_related('itens')
        .filter(ativo=True)
        .filter(status__in=STATUS_TAREFA_DASHBOARD_SEPARACAO)
        .filter(Q(nf__isnull=True) | ~Q(nf__status_fiscal=NotaFiscal.StatusFiscal.CANCELADA))
        .order_by('-updated_at', '-id')
    )
    if usuario is None or getattr(usuario, 'is_superuser', False):
        return tarefas
    setores_usuario = _setores_usuario_dashboard(usuario)
    if not setores_usuario:
        return []
    return [
        tarefa
        for tarefa in tarefas
        if _normalizar_setor_dashboard(tarefa.setor) in setores_usuario
    ]


def _status_separacao_item(item):
    if item.possui_restricao and item.tarefa.status == Tarefa.Status.FECHADO_COM_RESTRICAO:
        return 'COM RESTRICAO'
    if item.possui_restricao and item.tarefa.status == Tarefa.Status.LIBERADO_COM_RESTRICAO:
        return 'LIBERADO COM RESTRICAO'
    if item.possui_restricao and item.tarefa.status == Tarefa.Status.CONCLUIDO_COM_RESTRICAO:
        return 'CONCLUIDO COM RESTRICAO'
    if item.tarefa.status == Tarefa.Status.CONCLUIDO_COM_RESTRICAO:
        return 'CONCLUIDO COM RESTRICAO'
    if item.tarefa.status == Tarefa.Status.CONCLUIDO or item.quantidade_separada >= item.quantidade_total:
        return 'SEPARADO'
    if item.quantidade_separada > 0:
        return 'EM EXECUCAO'
    return 'AGUARDANDO'


def _task_reference_date(item):
    if item.nf_id and item.nf and item.nf.data_emissao:
        return timezone.localtime(item.nf.data_emissao).date()
    if item.tarefa.nf_id and item.tarefa.nf and item.tarefa.nf.data_emissao:
        return timezone.localtime(item.tarefa.nf.data_emissao).date()
    return timezone.localtime(item.tarefa.created_at).date()


def _data_referencia_item_dashboard(item):
    """Data para filtro de período: não esconde operação atual só porque a NF foi emitida há dias."""
    d_nf = _task_reference_date(item)
    if item.tarefa.created_at:
        d_tarefa = timezone.localtime(item.tarefa.created_at).date()
        return max(d_nf, d_tarefa)
    return d_nf


def _cliente_tarefa(item):
    fallback_sem_cliente = 'CLIENTE NAO INFORMADO'
    if not item:
        return ''

    nf = None
    try:
        if item.nf_id:
            nf = item.nf
        elif item.tarefa.nf_id:
            nf = item.tarefa.nf
    except ObjectDoesNotExist:
        logger.info(
            'Item sem NF consistente no dashboard da conferencia item_id=%s tarefa_id=%s',
            getattr(item, 'id', None),
            getattr(item, 'tarefa_id', None),
        )
        return fallback_sem_cliente

    if nf is None:
        return 'CONSOLIDADO'

    if not getattr(nf, 'cliente_id', None):
        logger.info(
            'Item sem cliente vinculado no dashboard da conferencia item_id=%s tarefa_id=%s nf_id=%s',
            getattr(item, 'id', None),
            getattr(item, 'tarefa_id', None),
            getattr(nf, 'id', None),
        )
        return fallback_sem_cliente

    try:
        cliente = nf.cliente
    except ObjectDoesNotExist:
        logger.info(
            'Item sem cliente vinculado no dashboard da conferencia item_id=%s tarefa_id=%s nf_id=%s cliente_id=%s',
            getattr(item, 'id', None),
            getattr(item, 'tarefa_id', None),
            getattr(nf, 'id', None),
            getattr(nf, 'cliente_id', None),
        )
        return fallback_sem_cliente

    return getattr(cliente, 'nome', '') or fallback_sem_cliente


def _nf_tarefa(item):
    if item.nf_id and item.nf:
        return str(item.nf.numero)
    if item.tarefa.nf_id and item.tarefa.nf:
        return str(item.tarefa.nf.numero)
    return resolve_nf_numero(obj=item, logger=logger, context='dashboard_separacao.item')


def _balcao_item_tarefa(item):
    if item.nf_id and item.nf:
        return bool(item.nf.balcao)
    if item.tarefa.nf_id and item.tarefa.nf:
        return bool(item.tarefa.nf.balcao)
    return False


def calcular_indicadores_volume_separacao(itens_filtrados):
    """Indicadores por volume (quantidades), alinhados às linhas da tabela do dashboard."""
    statuses_separado = {'SEPARADO', 'LIBERADO COM RESTRICAO', 'CONCLUIDO COM RESTRICAO'}
    total = sum(Decimal(item.quantidade_total) for item in itens_filtrados)
    separado = sum(
        Decimal(item.quantidade_total)
        for item in itens_filtrados
        if _status_separacao_item(item) in statuses_separado
    )
    pendente = total - separado
    if pendente < 0:
        pendente = Decimal('0')
    em_execucao = sum(1 for item in itens_filtrados if _status_separacao_item(item) == 'EM EXECUCAO')
    percentual = Decimal('0')
    percentual_pendente = Decimal('0')
    if total > 0:
        percentual = (separado / total * Decimal('100')).quantize(Decimal('0.01'))
        percentual_pendente = (pendente / total * Decimal('100')).quantize(Decimal('0.01'))
    return {
        'total': float(total),
        'separado': float(separado),
        'pendente': float(pendente),
        'em_execucao': int(em_execucao),
        'percentual': float(percentual),
        'percentual_pendente': float(percentual_pendente),
        'aguardando': float(pendente),
    }


def collect_itens_filtrados_dashboard_separacao(usuario, date_from, date_to, busca):
    """Mesma base de dados e filtros da tabela do dashboard de separação."""
    tarefa_ids_visiveis = [tarefa.id for tarefa in _tarefas_base_dashboard_separacao(usuario)]
    total_geral = len(tarefa_ids_visiveis)
    print('TOTAL GERAL:', total_geral)
    logger.info(
        'dashboard_separacao total_geral_tarefas=%s user=%s filtros=%s',
        total_geral,
        getattr(usuario, 'username', None),
        {'data_inicial': date_from.isoformat() if date_from else '', 'data_final': date_to.isoformat() if date_to else '', 'busca': busca},
    )
    if not tarefa_ids_visiveis:
        print('APÓS FILTRO:', 0)
        return []
    itens = list(
        TarefaItem.objects.select_related('tarefa', 'tarefa__nf', 'tarefa__nf__cliente', 'tarefa__rota', 'produto', 'nf', 'nf__cliente')
        .defer('nf__bairro', 'tarefa__nf__bairro')
        .filter(tarefa__ativo=True)
        .filter(tarefa_id__in=tarefa_ids_visiveis)
        .filter(Q(tarefa__nf__isnull=True) | ~Q(tarefa__nf__status_fiscal=NotaFiscal.StatusFiscal.CANCELADA))
        .filter(Q(nf__isnull=True) | ~Q(nf__status_fiscal=NotaFiscal.StatusFiscal.CANCELADA))
        .order_by('-tarefa__updated_at', 'tarefa_id', 'produto__cod_prod')
    )
    itens_filtrados = _filtrar_itens_separacao(itens, date_from, date_to, busca)
    print('APÓS FILTRO:', len(itens_filtrados))
    logger.info('dashboard_separacao itens_apos_filtro=%s', len(itens_filtrados))
    return itens_filtrados


def _resumo_status_separacao(itens):
    separado = Decimal('0')
    em_execucao = Decimal('0')
    aguardando = Decimal('0')
    for item in itens:
        status = _status_separacao_item(item)
        if status in {'SEPARADO', 'LIBERADO COM RESTRICAO', 'CONCLUIDO COM RESTRICAO'}:
            separado += item.quantidade_total
        elif status == 'EM EXECUCAO':
            em_execucao += item.quantidade_total
        else:
            aguardando += item.quantidade_total
    return {
        'total': float(separado + em_execucao + aguardando),
        'separado': float(separado),
        'em_execucao': float(em_execucao),
        'aguardando': float(aguardando),
    }


def _filtrar_itens_separacao(itens, date_from, date_to, busca):
    filtrados = []
    for item in itens:
        status = _status_separacao_item(item)
        cliente = _cliente_tarefa(item)
        nf_numero = _nf_tarefa(item)
        data_referencia = _data_referencia_item_dashboard(item)
        if date_from and data_referencia < date_from:
            continue
        if date_to and data_referencia > date_to:
            continue

        if busca:
            texto_busca = ' '.join(
                [
                    str(item.tarefa_id),
                    str(nf_numero or ''),
                    str(cliente or ''),
                    str(getattr(item.produto, 'cod_prod', '') or ''),
                    str(getattr(item.produto, 'descricao', '') or ''),
                    str(item.tarefa.rota.nome or ''),
                    'balcao' if _balcao_item_tarefa(item) else '',
                    str(status or ''),
                ]
            ).lower()
            if busca.isdigit():
                if busca not in str(nf_numero or ''):
                    continue
            elif busca not in texto_busca:
                continue
        filtrados.append(item)
    return filtrados


def _status_nf(nf, ultima_conferencia):
    if ultima_conferencia is None:
        return 'PENDENTE'
    if ultima_conferencia.status == Conferencia.Status.EM_CONFERENCIA:
        return 'EM CONFERENCIA'
    if ultima_conferencia.status == Conferencia.Status.AGUARDANDO:
        return 'PENDENTE'
    if ultima_conferencia.status == Conferencia.Status.OK:
        return 'CONCLUIDO'
    if ultima_conferencia.status == Conferencia.Status.DIVERGENCIA:
        return 'DIVERGENCIA'
    if ultima_conferencia.status == Conferencia.Status.LIBERADO_COM_RESTRICAO:
        return 'LIBERADO COM RESTRICAO'
    if ultima_conferencia.status == Conferencia.Status.CONCLUIDO_COM_RESTRICAO:
        return 'CONCLUIDO COM RESTRICAO'
    if nf.status == NotaFiscal.Status.BLOQUEADA_COM_RESTRICAO:
        return 'BLOQUEADA COM RESTRICAO'
    if nf.status == NotaFiscal.Status.LIBERADA_COM_RESTRICAO:
        return 'LIBERADA COM RESTRICAO'
    if nf.status == NotaFiscal.Status.CONCLUIDO_COM_RESTRICAO:
        return 'CONCLUIDO COM RESTRICAO'
    if nf.status == NotaFiscal.Status.CONCLUIDO:
        return 'CONCLUIDO'
    if nf.status == NotaFiscal.Status.EM_CONFERENCIA:
        return 'EM CONFERENCIA'
    if nf.status == NotaFiscal.Status.INCONSISTENTE:
        return 'INCONSISTENTE'
    return 'PENDENTE'


def _badge_status_class(status):
    if status == 'INCONSISTENTE':
        return 'status-badge--danger'
    if status in {'OK', 'CONCLUIDO', 'SEPARADO'}:
        return 'status-badge--success'
    if status in {'EM CONFERENCIA', 'EM EXECUCAO'}:
        return 'status-badge--warning'
    if status in {'DIVERGENCIA', 'LIBERADO COM RESTRICAO', 'LIBERADA COM RESTRICAO', 'CONCLUIDO COM RESTRICAO'}:
        return 'status-badge--info'
    if status == 'BLOQUEADA COM RESTRICAO':
        return 'status-badge--danger'
    if status == 'COM RESTRICAO':
        return 'status-badge--neutral'
    return 'status-badge--danger'


def _prioridade_status_dashboard(status):
    if status in {'INCONSISTENTE', 'AGUARDANDO', 'PENDENTE', 'FALTA SEPARAR', 'BLOQUEADA COM RESTRICAO', 'DIVERGENCIA'}:
        return 0
    if status in {'EM EXECUCAO', 'EM CONFERENCIA', 'LIBERADO COM RESTRICAO', 'LIBERADA COM RESTRICAO'}:
        return 1
    if status in {'SEPARADO', 'CONCLUIDO', 'CONCLUIDO COM RESTRICAO'}:
        return 2
    return 3


def _status_finalizado_dashboard(status):
    return status in {
        'SEPARADO',
        'CONCLUIDO',
        'CONCLUIDO COM RESTRICAO',
        'FINALIZADO',
    }


def _prioridade_operacional_dashboard(status, balcao=False):
    finalizado = _status_finalizado_dashboard(status)
    if balcao and not finalizado:
        return 0
    if not finalizado:
        return 1
    return 2


def _ultima_conferencia(nf):
    return nf.conferencias.exclude(status=Conferencia.Status.CANCELADA).order_by('-created_at').first()


def _nf_valida_dashboard_conferencia(nf, ultima_conferencia):
    if not separacao_concluida_nf(nf):
        return False
    return True


def _quantidade_separada_nf_item(nf, item_nf):
    if item_nf.produto_id is None:
        return Decimal('0')
    tarefa_item_nf = next(
        (
            tarefa_item
            for tarefa in nf.tarefas.all()
            for tarefa_item in tarefa.itens.all()
            if tarefa_item.produto_id == item_nf.produto_id and (not tarefa_item.nf_id or tarefa_item.nf_id == nf.id)
        ),
        None,
    )
    if tarefa_item_nf is not None:
        return min(tarefa_item_nf.quantidade_separada, item_nf.quantidade)

    tarefa_item_rota = (
        TarefaItem.objects.select_related('tarefa')
        .filter(
            tarefa__nf__isnull=True,
            tarefa__rota=nf.rota,
            nf=nf,
            produto=item_nf.produto,
        )
        .order_by('-updated_at')
        .first()
    )
    if tarefa_item_rota is None:
        return Decimal('0')
    return min(tarefa_item_rota.quantidade_separada, item_nf.quantidade)


def _nf_liberacao(liberacao):
    if liberacao.nf_id and liberacao.nf:
        return str(liberacao.nf.numero), liberacao.nf_id
    if liberacao.tarefa_id and liberacao.tarefa and liberacao.tarefa.nf_id:
        return str(liberacao.tarefa.nf.numero), liberacao.tarefa.nf_id
    if liberacao.tarefa_id and liberacao.tarefa:
        nfs = sorted(
            {
                item.nf.numero
                    for item in liberacao.tarefa.itens.select_related('nf').defer('nf__bairro').all()
                if item.nf_id and item.nf
            }
        )
        if nfs:
            return ', '.join(nfs), None
    return (liberacao.nf_numero or '-'), None


@require_profiles(Usuario.Perfil.GESTOR)
def dashboard_separacao(request):
    date_from, date_to, busca = _resolver_periodo_e_busca(request, default_today=True)
    itens_filtrados = collect_itens_filtrados_dashboard_separacao(request.user, date_from, date_to, busca)
    indicadores = calcular_indicadores_volume_separacao(itens_filtrados)
    logger.info(
        'dashboard_separacao indicadores total=%s separado=%s pendente=%s em_execucao=%s filtros=%s',
        indicadores['total'],
        indicadores['separado'],
        indicadores['pendente'],
        indicadores['em_execucao'],
        {
            'data_inicial': date_from.isoformat() if date_from else '',
            'data_final': date_to.isoformat() if date_to else '',
            'busca': busca,
        },
    )
    linhas = []
    for item in itens_filtrados:
        status_item = _status_separacao_item(item)
        linhas.append(
            {
                'tarefa_id': item.tarefa_id,
                'nf': _nf_tarefa(item),
                'nf_id': item.nf_id or item.tarefa.nf_id,
                'rota': f'Balcao - {item.tarefa.rota.nome}' if _balcao_item_tarefa(item) else item.tarefa.rota.nome,
                'cliente': _cliente_tarefa(item),
                'balcao': _balcao_item_tarefa(item),
                'produto': getattr(item.produto, 'cod_prod', '') or '',
                'descricao': getattr(item.produto, 'descricao', '') or '',
                'quantidade': float(item.quantidade_total),
                'status': status_item,
                'status_badge_class': _badge_status_class(status_item),
                'pode_liberar': item.possui_restricao and item.tarefa.status == Tarefa.Status.FECHADO_COM_RESTRICAO,
                'pode_excluir': item.tarefa.status not in {Tarefa.Status.CONCLUIDO, Tarefa.Status.CONCLUIDO_COM_RESTRICAO},
                '_prioridade': _prioridade_operacional_dashboard(status_item, _balcao_item_tarefa(item)),
                '_prioridade_status': _prioridade_status_dashboard(status_item),
                '_updated_at': item.tarefa.updated_at,
            }
        )
    linhas.sort(key=lambda linha: (linha['_prioridade'], linha['_prioridade_status'], -linha['_updated_at'].timestamp()))
    for linha in linhas:
        linha.pop('_prioridade', None)
        linha.pop('_prioridade_status', None)
        linha.pop('_updated_at', None)
    paginacao = _paginar_lista(request, linhas)
    contexto = {
        'indicadores': indicadores,
        'linhas': paginacao['page_obj'],
        'filtros': {
            'date_from': date_from.isoformat() if date_from else '',
            'date_to': date_to.isoformat() if date_to else '',
            'busca': request.GET.get('busca', request.GET.get('q', '')),
        },
        'detalhe_nf': _build_detalhe_nf_context(request, request.GET.get('nf_detalhe')),
        **paginacao,
    }
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' and request.GET.get('partial') == 'table':
        return _render(request, 'partials/dashboard_separacao_tabela.html', contexto)
    return _render(request, 'dashboard_separacao.html', contexto)


@require_profiles(Usuario.Perfil.GESTOR)
def dashboard_conferencia(request):
    date_from, date_to, busca = _resolver_periodo_e_busca(request, default_today=True)
    nfs_disponiveis = get_nfs_monitoramento_conferencia(
        request.user,
        data_inicio=date_from,
        data_fim=date_to,
        busca=busca,
    )
    nf_ids = [nf.get('id') for nf in nfs_disponiveis if nf.get('id') is not None]
    nf_por_id = {
        nf.id: nf
        for nf in NotaFiscal.objects.select_related('cliente', 'rota').defer('bairro')
        .prefetch_related('itens__produto')
        .filter(id__in=nf_ids)
    }

    linhas = []
    for nf_data in nfs_disponiveis:
        nf = nf_por_id.get(nf_data['id'])
        if nf is None:
            continue
        data_referencia = timezone.localtime(nf.created_at).date() if nf.created_at else (
            nf.data_emissao.date() if nf.data_emissao else timezone.localdate()
        )
        if date_from and data_referencia < date_from:
            continue
        if date_to and data_referencia > date_to:
            continue

        status_raw = (nf_data.get('status') or 'PENDENTE').strip().upper()
        status = status_raw.replace('_', ' ')
        progresso = nf_data.get('progresso') or {}
        esperados = float(progresso.get('esperado') or 0)
        conferidos = float(progresso.get('conferido') or 0)
        faltantes = max(int(nf_data.get('itens_pendentes_conferencia') or 0), 0)
        linhas.append(
            {
                'nf_id': nf_data.get('id'),
                'numero': nf_data.get('numero'),
                'cliente': nf_data.get('cliente'),
                'rota': nf_data.get('rota'),
                'balcao': bool(nf_data.get('balcao')),
                'itens': int(esperados),
                'conferidos': int(conferidos),
                'faltantes': faltantes,
                # Lista operacional de conferência só inclui NFs com separação pronta.
                'separacao_pendente': 'NAO',
                'status': status,
                'status_badge_class': _badge_status_class(status),
                'pode_liberar': status in {'DIVERGENCIA', 'BLOQUEADA COM RESTRICAO'},
                'pode_excluir': status not in {'CONCLUIDO', 'CONCLUIDO COM RESTRICAO'},
                '_prioridade': _prioridade_operacional_dashboard(status, bool(nf_data.get('balcao'))),
                '_prioridade_status': _prioridade_status_dashboard(status),
                '_updated_at': nf.updated_at,
            }
        )

    logger.debug(
        'dashboard_conferencia consistencia total_lista_operacional=%s total_dashboard_filtrado=%s filtros=%s',
        len(nfs_disponiveis),
        len(linhas),
        {
            'data_inicial': date_from.isoformat() if date_from else '',
            'data_final': date_to.isoformat() if date_to else '',
            'busca': busca,
        },
    )

    linhas.sort(
        key=lambda linha: (
            linha['_prioridade'],
            linha['_prioridade_status'],
            -linha['_updated_at'].timestamp(),
        )
    )
    for linha in linhas:
        linha.pop('_prioridade', None)
        linha.pop('_prioridade_status', None)
        linha.pop('_updated_at', None)

    total_nfs = len(linhas)
    conferidas = sum(1 for linha in linhas if linha['status'] in {'CONCLUIDO', 'CONCLUIDO COM RESTRICAO', 'OK'})
    divergencias = sum(1 for linha in linhas if linha['status'] in {'DIVERGENCIA', 'BLOQUEADA COM RESTRICAO'})
    pendentes = max(total_nfs - conferidas, 0)
    percentual_concluido = 0 if total_nfs == 0 else round(conferidas / total_nfs * 100, 2)

    itens_separacao = list(
        TarefaItem.objects.select_related('tarefa', 'tarefa__nf', 'tarefa__nf__cliente', 'produto', 'nf', 'nf__cliente')
        .defer('nf__bairro', 'tarefa__nf__bairro')
        .filter(tarefa__ativo=True)
        .filter(Q(tarefa__nf__isnull=True) | ~Q(tarefa__nf__status_fiscal=NotaFiscal.StatusFiscal.CANCELADA))
    )
    itens_separacao_filtrados = _filtrar_itens_separacao(itens_separacao, date_from, date_to, busca)
    paginacao = _paginar_lista(request, linhas)

    contexto = {
        'indicadores': {
            'total_nfs': total_nfs,
            'conferidas': conferidas,
            'percentual_concluido': percentual_concluido,
            'divergencias': divergencias,
            'pendentes': pendentes,
        },
        'resumo_separacao': _resumo_status_separacao(itens_separacao_filtrados),
        'linhas': paginacao['page_obj'],
        'filtros': {
            'date_from': date_from.isoformat() if date_from else '',
            'date_to': date_to.isoformat() if date_to else '',
            'busca': request.GET.get('busca', request.GET.get('q', '')),
        },
        'detalhe_nf': _build_detalhe_nf_context(request, request.GET.get('nf_detalhe')),
        **paginacao,
    }
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' and request.GET.get('partial') == 'table':
        return _render(request, 'partials/dashboard_conferencia_tabela.html', contexto)
    return _render(request, 'dashboard_conferencia.html', contexto)


@require_profiles(Usuario.Perfil.GESTOR)
def detalhe_nf_por_id(request, nf_id):
    # Fluxo operacional manual: detalhes de conferencia nao sao mais abertos por redirecionamento automatico.
    return redirect('web-conferencia-lista')


@require_profiles(Usuario.Perfil.GESTOR)
def detalhe_nf(request, nf_numero):
    numeros = [numero.strip() for numero in str(nf_numero).split(',') if numero.strip()]
    if not numeros:
        return _render(
            request,
            'conferencia_erro.html',
            {'mensagem': 'Nenhuma NF informada para consulta.'},
        )

    nfs = list(
        NotaFiscal.objects.select_related('cliente', 'rota').defer('bairro').prefetch_related(
            'itens__produto',
            Prefetch(
                'tarefas',
                queryset=Tarefa.objects.prefetch_related(
                    Prefetch(
                        'itens',
                        queryset=TarefaItem.objects.select_related('produto', 'nf').defer('nf__bairro'),
                    )
                ),
            ),
            Prefetch('conferencias', queryset=Conferencia.objects.prefetch_related('itens__produto').order_by('-created_at')),
        )
        .filter(numero__in=numeros, ativa=True)
        .order_by('-id')
    )
    encontrados = {str(numero) for numero in (nf.numero for nf in nfs)}
    nao_encontrados = [numero for numero in numeros if numero not in encontrados]

    detalhes = []
    for numero in numeros:
        nf = next((item for item in nfs if str(item.numero) == numero), None)
        if nf is None:
            continue

        if nf.status_fiscal == NotaFiscal.StatusFiscal.CANCELADA:
            nao_encontrados.append(numero)
            continue

        ultima = _ultima_conferencia(nf)
        itens_conferencia = {item.produto_id: item for item in (ultima.itens.all() if ultima else [])}
        linhas = []
        pendencias_separacao = 0

        itens_nf_ordenados = sorted(
            nf.itens.all(),
            key=lambda item: (((getattr(item.produto, 'setor', None) or 'ZZZ')), (item.codigo_operacional or '')),
        )
        for item_nf in itens_nf_ordenados:
            separado = _quantidade_separada_nf_item(nf, item_nf)
            conferencia_item = itens_conferencia.get(item_nf.produto_id)
            conferido = conferencia_item.qtd_conferida if conferencia_item else Decimal('0')
            falta = max(item_nf.quantidade - conferido, Decimal('0'))

            if item_nf.produto_id is None:
                status = 'PRODUTO NAO CADASTRADO'
                pendencias_separacao += 1
            elif conferencia_item and conferencia_item.status == ConferenciaItem.Status.DIVERGENCIA:
                status = 'DIVERGENCIA'
            elif separado < item_nf.quantidade:
                status = 'FALTA SEPARAR'
                pendencias_separacao += 1
            elif falta > 0:
                status = 'AGUARDANDO'
            else:
                status = 'OK'

            linhas.append(
                {
                    'produto': item_nf.codigo_operacional,
                    'descricao': item_nf.descricao_operacional,
                    'setor': getattr(item_nf.produto, 'setor', '') or '',
                    'qtd_nf': float(item_nf.quantidade),
                    'separado': float(separado),
                    'conferido': float(conferido),
                    'falta': float(falta),
                    'status': status,
                }
            )

        status_nf = _status_nf(nf, ultima)
        detalhes.append(
            {
                'nf': nf,
                'linhas': linhas,
                'pendencias_separacao': pendencias_separacao,
                'status_nf': status_nf,
                'status_nf_badge_class': _badge_status_class(status_nf),
                'pode_liberar_divergencia': bool(
                    (ultima and ultima.status == Conferencia.Status.DIVERGENCIA)
                    or nf.status == NotaFiscal.Status.BLOQUEADA_COM_RESTRICAO
                ),
            }
        )

    if not detalhes:
        return _render(
            request,
            'conferencia_erro.html',
            {'mensagem': 'Nenhuma NF encontrada para os numeros informados.'},
        )

    return _render(
        request,
        'conferencia_detalhe.html',
        {
            'detalhes': detalhes,
            'nao_encontrados': nao_encontrados,
            'total_encontrados': len(detalhes),
            'total_nao_encontrados': len(nao_encontrados),
        },
    )


@require_profiles(Usuario.Perfil.GESTOR)
def relatorio_liberacoes(request):
    liberacoes = (
        LiberacaoDivergencia.objects.select_related('usuario', 'nf', 'tarefa', 'tarefa__nf', 'nf__cliente')
        .defer('nf__bairro', 'tarefa__nf__bairro')
        .prefetch_related('tarefa__itens__nf')
        .all()
    )

    data_filtro = _parse_date(request.GET.get('data'))
    usuario_filtro = (request.GET.get('usuario') or '').strip().lower()
    nf_filtro = (request.GET.get('busca') or request.GET.get('nf') or '').strip().lower()

    if data_filtro:
        liberacoes = liberacoes.filter(created_at__date=data_filtro)
    if usuario_filtro:
        liberacoes = liberacoes.filter(Q(usuario__username__icontains=usuario_filtro) | Q(usuario__nome__icontains=usuario_filtro))
    if nf_filtro:
        liberacoes = liberacoes.filter(
            Q(nf_numero__icontains=nf_filtro)
            | Q(nf__numero__icontains=nf_filtro)
            | Q(tarefa__nf__numero__icontains=nf_filtro)
        )

    linhas = []
    for liberacao in liberacoes:
        nf_numero, nf_id = _nf_liberacao(liberacao)
        linhas.append(
            {
                'data': liberacao.created_at,
                'usuario': liberacao.usuario.nome or liberacao.usuario.username,
                'nf': nf_numero,
                'nf_id': nf_id,
                'tarefa': liberacao.tarefa_id or '-',
                'status_anterior': liberacao.status_anterior,
                'status_novo': liberacao.status_novo,
                'motivo': liberacao.motivo,
            }
        )
    paginacao = _paginar_lista(request, linhas)

    return _render(
        request,
        'relatorio_liberacoes.html',
        {
            'linhas': paginacao['page_obj'],
            'filtros': {
                'data': request.GET.get('data', ''),
                'usuario': request.GET.get('usuario', ''),
                'busca': request.GET.get('busca', request.GET.get('nf', '')),
            },
            **paginacao,
        },
    )