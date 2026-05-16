"""
Janelas operacionais por contexto.

- Listas (separação/conferência): hoje + ontem, sem filtros visíveis.
- Dashboards: somente hoje por padrão.
- Histórico: pesquisa avançada (query string manual).
"""

from datetime import date, timedelta

from django.utils import timezone


def periodo_operacional_padrao():
    """Listas operacionais: ontem até hoje."""
    hoje = timezone.localdate()
    ontem = hoje - timedelta(days=1)
    return ontem, hoje


def periodo_dashboard_padrao():
    """Dashboards: somente o dia atual."""
    hoje = timezone.localdate()
    return hoje, hoje


def parse_date_param(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def usuario_informou_periodo(request):
    return bool(
        (request.GET.get('date_from') or request.GET.get('data_inicial') or '').strip()
        or (request.GET.get('date_to') or request.GET.get('data_final') or '').strip()
    )


def _resolver_periodo_request(request, padrao_fn):
    date_from = parse_date_param(request.GET.get('date_from') or request.GET.get('data_inicial'))
    date_to = parse_date_param(request.GET.get('date_to') or request.GET.get('data_final'))
    busca = (request.GET.get('busca') or request.GET.get('q') or '').strip().lower()

    if date_from is None and date_to is None:
        date_from, date_to = padrao_fn()
    else:
        padrao_inicio, padrao_fim = padrao_fn()
        if date_from is None:
            date_from = padrao_inicio
        if date_to is None:
            date_to = padrao_fim

    if date_to < date_from:
        date_to = date_from
    return date_from, date_to, busca


def resolver_periodo_operacional_request(request):
    """Separação e conferência (listas): ontem até hoje."""
    return _resolver_periodo_request(request, periodo_operacional_padrao)


def resolver_periodo_dashboard_request(request):
    """Dashboards: somente hoje, salvo filtro manual."""
    return _resolver_periodo_request(request, periodo_dashboard_padrao)


def filtros_template_periodo(date_from, date_to, busca=''):
    return {
        'date_from': date_from.isoformat() if date_from else '',
        'date_to': date_to.isoformat() if date_to else '',
        'busca': busca or '',
        'periodo_padrao': not busca,
    }


def filtrar_queryset_created_at(queryset, date_from, date_to, campo='created_at'):
    if date_from is not None:
        queryset = queryset.filter(**{f'{campo}__date__gte': date_from})
    if date_to is not None:
        queryset = queryset.filter(**{f'{campo}__date__lte': date_to})
    return queryset
