from django.conf import settings
from django.core.paginator import Paginator
from django.shortcuts import render
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.operacional_periodo import filtros_template_periodo, resolver_periodo_operacional_request
from apps.core.services.minuta_service import (
    consultar_minuta_itens_queryset,
    obter_cards_minuta,
    serializar_linha_minuta_item,
)
from apps.usuarios.access import PerfilPermitido
from apps.usuarios.models import Usuario


def _minuta_ajax_partial(request):
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return None
    return (request.GET.get('partial') or '').strip()


def _pagination_query(request):
    params = request.GET.copy()
    params.pop('partial', None)
    params.pop('page', None)
    query = params.urlencode()
    return f'&{query}' if query else ''


def resolver_filtros_minuta_request(request):
    date_from, date_to, busca_periodo = resolver_periodo_operacional_request(request)
    return {
        'romaneio': (request.GET.get('romaneio') or '').strip(),
        'status': (request.GET.get('status') or '').strip(),
        'busca': (request.GET.get('busca') or '').strip() or busca_periodo,
        'date_from': date_from,
        'date_to': date_to,
        **filtros_template_periodo(date_from, date_to, busca_periodo),
    }


def _filtros_consulta_minuta(filtros):
    return {
        'romaneio': filtros.get('romaneio', ''),
        'status': filtros.get('status', ''),
        'busca': filtros.get('busca', ''),
        'data_inicio': filtros.get('date_from'),
        'data_fim': filtros.get('date_to'),
    }


def contexto_minuta_tabela(request, filtros):
    queryset = consultar_minuta_itens_queryset(**_filtros_consulta_minuta(filtros))
    page_size = int(getattr(settings, 'OPERATIONAL_PAGE_SIZE', 50))
    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(request.GET.get('page'))
    linhas = [serializar_linha_minuta_item(item) for item in page_obj.object_list]
    return {
        'linhas': linhas,
        'filtros': filtros,
        'page_obj': page_obj,
        'is_paginated': page_obj.has_other_pages(),
        'pagination_query': _pagination_query(request),
    }


def render_minuta_tabela_partial(request, filtros=None):
    if filtros is None:
        filtros = resolver_filtros_minuta_request(request)
    return render(request, 'partials/minuta_tabela.html', contexto_minuta_tabela(request, filtros))


class MinutaCardsAPIView(APIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.GESTOR,)

    def get(self, request):
        filtros = resolver_filtros_minuta_request(request)
        payload = obter_cards_minuta(**_filtros_consulta_minuta(filtros))
        return Response(payload)


class MinutaListaAPIView(APIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.GESTOR,)

    def get(self, request):
        return render_minuta_tabela_partial(request)


class MinutaInconsistenciasAPIView(APIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.GESTOR,)

    def get(self, request):
        filtros = resolver_filtros_minuta_request(request)
        payload = obter_cards_minuta(**_filtros_consulta_minuta(filtros))
        return Response({'minuta_inconsistencias': payload['minuta_inconsistencias']})


class MinutaDuplicidadesAPIView(APIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.GESTOR,)

    def get(self, request):
        filtros = resolver_filtros_minuta_request(request)
        payload = obter_cards_minuta(**_filtros_consulta_minuta(filtros))
        return Response(
            {
                'duplicados': payload['resumo'].get('duplicados', 0),
                'minuta_inconsistencias': {
                    'duplicidades': payload['minuta_inconsistencias'].get('duplicidades', 0),
                },
            }
        )


def minuta_cards_inconsistencias_json(filtros):
    payload = obter_cards_minuta(**_filtros_consulta_minuta(filtros))
    return json.dumps(payload['minuta_inconsistencias'])
