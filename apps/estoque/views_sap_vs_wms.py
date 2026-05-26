import logging

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import connection
from django.db.utils import ProgrammingError
from django.shortcuts import redirect
from django.urls import reverse

from apps.estoque.db_schema import aplicar_schema_estoque_brownfield, tabelas_estoque_existem
from apps.estoque.models import SapVsWmsUpload
from apps.estoque.services.sap_vs_wms import (
    SapVsWmsError,
    calcular_metricas,
    importar_planilha_sap,
    listar_setores_disponiveis,
    montar_linhas_conciliacao,
)
from apps.estoque.views_web import MSG_SCHEMA_PENDENTE, PAGE_SIZE, _garantir_schema_estoque, _render
from apps.usuarios.access import require_profiles
from apps.usuarios.models import Usuario

logger = logging.getLogger(__name__)


@require_profiles(Usuario.Perfil.GESTOR)
def estoque_sap_vs_wms_web(request):
    if not _garantir_schema_estoque():
        messages.error(request, MSG_SCHEMA_PENDENTE)
        return _render(request, 'estoque/schema_pendente.html', {'comando': 'migrate estoque --noinput'})

    if request.method == 'POST' and request.POST.get('acao') == 'upload':
        arquivo = request.FILES.get('arquivo')
        if not arquivo:
            messages.error(request, 'Selecione um arquivo Excel (.xlsx).')
            return redirect('web-estoque-sap-vs-wms')
        nome = (arquivo.name or '').lower()
        if not nome.endswith(('.xlsx', '.xls')):
            messages.error(request, 'Formato inválido. Envie planilha .xlsx ou .xls.')
            return redirect('web-estoque-sap-vs-wms')
        try:
            total = importar_planilha_sap(arquivo, request.user)
            messages.success(request, f'Upload SAP concluído: {total} produto(s) importado(s).')
        except SapVsWmsError as exc:
            messages.error(request, str(exc))
        except ProgrammingError as exc:
            logger.exception('SAP_VS_WMS_UPLOAD_ERRO: %s', exc)
            messages.error(request, MSG_SCHEMA_PENDENTE)
        except Exception as exc:
            logger.exception('SAP_VS_WMS_UPLOAD_INESPERADO: %s', exc)
            messages.error(request, 'Falha ao importar planilha SAP.')
        return redirect('web-estoque-sap-vs-wms')

    busca = (request.GET.get('busca') or '').strip()
    setor = (request.GET.get('setor') or '').strip()

    try:
        linhas = montar_linhas_conciliacao(busca=busca, setor=setor)
        metricas = calcular_metricas(linhas)
        paginator = Paginator(linhas, PAGE_SIZE)
        page_obj = paginator.get_page(request.GET.get('page'))
        ultimo_upload = SapVsWmsUpload.objects.order_by('-created_at').values_list('created_at', flat=True).first()
        total_sap = SapVsWmsUpload.objects.count()
    except ProgrammingError as exc:
        logger.exception('SAP_VS_WMS_QUERY_ERRO: %s', exc)
        if connection.vendor == 'postgresql' and not tabelas_estoque_existem(connection):
            aplicar_schema_estoque_brownfield(connection)
        messages.error(request, MSG_SCHEMA_PENDENTE)
        return _render(request, 'estoque/schema_pendente.html', {'comando': 'migrate estoque --noinput'})

    query_parts = []
    if busca:
        query_parts.append(f'busca={busca}')
    if setor:
        query_parts.append(f'setor={setor}')

    return _render(
        request,
        'estoque/sap_vs_wms.html',
        {
            'page_obj': page_obj,
            'linhas': page_obj.object_list,
            'is_paginated': page_obj.has_other_pages(),
            'pagination_query': '&'.join(query_parts),
            'busca': busca,
            'setor': setor,
            'setores': listar_setores_disponiveis(),
            'metricas': metricas,
            'ultimo_upload': ultimo_upload,
            'total_sap': total_sap,
        },
    )
