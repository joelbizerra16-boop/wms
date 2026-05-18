import json
import logging
import time

from django.conf import settings
from django.shortcuts import render
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.operacional_periodo import filtros_template_periodo, resolver_periodo_operacional_request
from apps.core.services.minuta_service import (
    buscar_vinculo_nf_historico,
    consultar_minuta_itens_queryset,
    consulta_minuta_historica_ativa,
    limite_operacional_minuta,
    obter_cards_minuta,
    serializar_linha_minuta_item,
    serializar_vinculo_nf_item,
)
from apps.usuarios.access import PerfilPermitido
from apps.usuarios.models import Usuario


logger = logging.getLogger(__name__)


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
    inicio = time.perf_counter()
    queryset = consultar_minuta_itens_queryset(**_filtros_consulta_minuta(filtros))
    historico = consulta_minuta_historica_ativa(filtros.get('romaneio', ''), filtros.get('busca', ''))
    page_size = limite_operacional_minuta(filtros.get('romaneio', ''), filtros.get('busca', ''))
    configured_page_size = int(getattr(settings, 'OPERATIONAL_PAGE_SIZE', page_size) or page_size)
    page_size = min(page_size, configured_page_size) if configured_page_size > 0 else page_size
    try:
        current_page = max(int(request.GET.get('page') or '1'), 1)
    except ValueError:
        current_page = 1
    offset = (current_page - 1) * page_size
    consulta_inicio = time.perf_counter()
    registros = list(queryset[offset:offset + page_size + 1])
    consulta_ms = round((time.perf_counter() - consulta_inicio) * 1000, 2)
    has_next = len(registros) > page_size
    registros = registros[:page_size]
    serializacao_inicio = time.perf_counter()
    linhas = [serializar_linha_minuta_item(item) for item in registros]
    serializacao_ms = round((time.perf_counter() - serializacao_inicio) * 1000, 2)
    total_ms = round((time.perf_counter() - inicio) * 1000, 2)
    logger.warning(
        'MINUTA_QUERY_MS etapa=lista total_ms=%s sql_ms=%s serial_ms=%s registros=%s busca=%s page=%s historico=%s',
        total_ms,
        consulta_ms,
        serializacao_ms,
        len(linhas),
        filtros.get('busca') or filtros.get('romaneio') or '',
        current_page,
        historico,
    )
    return {
        'linhas': linhas,
        'filtros': filtros,
        'current_page': current_page,
        'has_previous': current_page > 1,
        'previous_page': current_page - 1,
        'has_next': has_next,
        'next_page': current_page + 1,
        'is_paginated': current_page > 1 or has_next,
        'pagination_query': _pagination_query(request),
    }


def render_minuta_tabela_partial(request, filtros=None):
    inicio = time.perf_counter()
    if filtros is None:
        filtros = resolver_filtros_minuta_request(request)
    response = render(request, 'partials/minuta_tabela.html', contexto_minuta_tabela(request, filtros))
    total_ms = round((time.perf_counter() - inicio) * 1000, 2)
    logger.warning(
        'MINUTA_QUERY_MS etapa=render total_ms=%s busca=%s',
        total_ms,
        filtros.get('busca') or filtros.get('romaneio') or '',
    )
    return response


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


class MinutaHistoricoNFAPIView(APIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.GESTOR,)

    def get(self, request):
        numero = (request.GET.get('numero') or request.GET.get('nf') or '').strip()
        if not numero:
            return Response({'erro': 'Informe o número da NF.'}, status=400)
        item = buscar_vinculo_nf_historico(numero)
        if item is None:
            return Response({'encontrado': False, 'vinculo': None})
        return Response({'encontrado': True, 'vinculo': serializar_vinculo_nf_item(item)})


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
