import logging

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import render

from apps.recebimento.models import EstoqueTemporario
from apps.recebimento.services.importador_recebimento import importar_xml_recebimento
from apps.recebimento.services.xml_parser import RecebimentoXMLError
from apps.usuarios.access import require_profiles
from apps.usuarios.models import Usuario

logger = logging.getLogger(__name__)

MAX_XML_RECEBIMENTO = 50


@require_profiles(Usuario.Perfil.GESTOR)
def recebimento_importar_xml_web(request):
    resultados = None
    if request.method == 'POST':
        xml_files = request.FILES.getlist('xml_files')
        resultados = {'sucesso': 0, 'erros': 0, 'detalhes': []}
        if not xml_files:
            messages.error(request, 'Selecione ao menos um arquivo XML.')
        elif len(xml_files) > MAX_XML_RECEBIMENTO:
            messages.error(request, f'Limite de {MAX_XML_RECEBIMENTO} arquivos por envio.')
        else:
            for xml_file in xml_files:
                nome = getattr(xml_file, 'name', 'arquivo.xml')
                if not nome.lower().endswith('.xml'):
                    resultados['erros'] += 1
                    resultados['detalhes'].append(
                        {'arquivo': nome, 'status': 'erro', 'mensagem': 'Extensão inválida.'}
                    )
                    continue
                try:
                    info = importar_xml_recebimento(xml_file, usuario=request.user, nome_arquivo=nome)
                    resultados['sucesso'] += 1
                    resultados['detalhes'].append(
                        {
                            'arquivo': nome,
                            'status': 'sucesso',
                            'nf': info['nf_numero'],
                            'chave_nfe': info['chave_nfe'],
                            'mensagem': f"{info['quantidade_itens']} item(ns) → estoque TEMP.",
                        }
                    )
                except RecebimentoXMLError as exc:
                    resultados['erros'] += 1
                    resultados['detalhes'].append(
                        {'arquivo': nome, 'status': 'erro', 'mensagem': str(exc)}
                    )
                except Exception as exc:
                    logger.exception('RECEBIMENTO_XML_FALHA arquivo=%s', nome)
                    resultados['erros'] += 1
                    resultados['detalhes'].append(
                        {'arquivo': nome, 'status': 'erro', 'mensagem': str(exc)}
                    )
            if resultados['sucesso']:
                messages.success(
                    request,
                    f'{resultados["sucesso"]} XML(s) recebidos no estoque temporário.',
                )
            if resultados['erros']:
                messages.warning(request, f'{resultados["erros"]} arquivo(s) com erro.')

    return render(
        request,
        'recebimento/importar_xml.html',
        {'resultados': resultados},
    )


@require_profiles(Usuario.Perfil.GESTOR)
def recebimento_estoque_temporario_web(request):
    qs = (
        EstoqueTemporario.objects.filter(status=EstoqueTemporario.Status.TEMP)
        .select_related('usuario_recebimento')
        .order_by('-data_recebimento', 'nf_numero', 'produto_codigo')
    )
    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(
        request,
        'recebimento/estoque_temporario.html',
        {
            'page_obj': page_obj,
            'itens': page_obj.object_list,
            'is_paginated': page_obj.has_other_pages(),
            'pagination_query': '',
            'total_temp': paginator.count,
        },
    )


@require_profiles(Usuario.Perfil.GESTOR)
def recebimento_ativacao_scan_web(request):
    return render(request, 'recebimento/ativacao_scan.html')
