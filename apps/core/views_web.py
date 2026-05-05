import logging
from datetime import datetime

import pandas as pd
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Prefetch, Q
import json

from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.clientes.models import Cliente
from apps.conferencia.models import Conferencia
from apps.conferencia.services.conferencia_service import (
    ConferenciaError,
    bipar_conferencia,
    finalizar_conferencia,
    iniciar_conferencia,
    listar_nfs_disponiveis,
)
from apps.nf.models import EntradaNF, NotaFiscal
from apps.nf.services.status_service import atualizar_status_nf
from apps.nf.services.importador_xml import (
    ImportacaoXMLError,
    analisar_xml_nfe,
    extrair_resumo_nfe_xml,
    importar_xml_nfe,
)
from apps.nf.services.xml_storage_service import XMLStorageUnavailableError, open_entrada_xml
from apps.nf.services.limpeza_importacao_service import (
    LimpezaImportacaoError,
    executar_limpeza_importacao_controlada,
)
from apps.core.services.produto_sync_service import sincronizar_referencias_produto
from apps.logs.models import Log
from apps.produtos.models import GrupoAgregado, Produto
from apps.rotas.models import Rota
from apps.tarefas.models import Tarefa
from apps.tarefas.services.separacao_service import (
    SeparacaoError,
    bipar_tarefa,
    finalizar_tarefa,
    iniciar_tarefa,
    liberar_execucao_tarefa,
    listar_itens_tarefa_para_exibicao_seguro,
    listar_tarefas_disponiveis,
)
from apps.tarefas.separacao_views import OPERACIONAL_STATUS_BLOQUEADO, OPERACIONAL_STATUS_BLOQUEADO_ERRO
from apps.core.nf_utils import resolve_nf_numero
from apps.core.services.cadastro_import_service import importar_clientes_arquivo, importar_produtos_arquivo, importar_rotas_arquivo
from apps.usuarios.access import build_access_context, require_profiles
from apps.usuarios.models import Setor, Usuario
from apps.usuarios.forms import UsuarioForm

logger = logging.getLogger(__name__)
SCAN_SESSION_KEY = 'scan_entradas_nf_ids'
MAX_XML_FILES_POR_ENVIO = 500
MAX_XML_FILES_POR_LOTE = 100


def _bool_post(request, key, default=False):
    return request.POST.get(key) in {'1', 'on', 'true', 'True'} if key in request.POST else default


def _normalizar_campo(valor):
    valor = (valor or '').strip()
    return valor or None


def _normalizar_texto_planilha(valor):
    if valor is None or pd.isna(valor):
        return ''
    return str(valor).strip()


def _ler_planilha_upload(arquivo):
    nome = (getattr(arquivo, 'name', '') or '').lower()
    if nome.endswith('.csv'):
        return pd.read_csv(arquivo)
    return pd.read_excel(arquivo)


def _coluna_linha(row, aliases):
    normalized = {str(col).strip().upper(): col for col in row.index}
    for alias in aliases:
        col = normalized.get(alias.upper())
        if col is not None:
            return row.get(col)
    return None


def _normalizar_setor_produto(valor):
    setor = (valor or '').strip().upper()
    if not setor:
        return None
    return setor


def _normalizar_codigo_leitura(valor):
    codigo = ''.join(str(valor or '').strip().split())
    if not codigo:
        return None
    return codigo.upper()


def _categoria_por_setor_produto(setor):
    mapa = {
        'FILTRO': Produto.Categoria.FILTROS,
        'FILTROS': Produto.Categoria.FILTROS,
        'LUBRIFICANTE': Produto.Categoria.LUBRIFICANTE,
        'AGREGADO': Produto.Categoria.AGREGADO,
        'NAO ENCONTRADO': Produto.Categoria.NAO_ENCONTRADO,
        'NAO_ENCONTRADO': Produto.Categoria.NAO_ENCONTRADO,
    }
    return mapa.get((setor or '').strip().upper(), Produto.Categoria.NAO_ENCONTRADO)


def _vincular_grupo_agregado_produto(produto):
    setor = (produto.setor or '').strip().upper()
    if not setor:
        return
    grupo, _ = GrupoAgregado.objects.get_or_create(nome=setor)
    produto.grupos_agregados.add(grupo)


def _render(request, template_name, context=None):
    base_context = {'usuario': request.user}
    base_context.update(build_access_context(request.user))
    if context:
        base_context.update(context)
    return render(request, template_name, base_context)


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


def _resultado_erro_importacao(mensagem, chave_nfe='-', arquivo=None):
    detalhe_arquivo = f'Arquivo: {arquivo}' if arquivo else None
    return {
        'status': 'erro',
        'mensagem': mensagem if not detalhe_arquivo else f'{mensagem} ({detalhe_arquivo})',
        'chave_nfe': chave_nfe,
    }


def _scan_ids_session(request):
    ids = request.session.get(SCAN_SESSION_KEY, [])
    if not isinstance(ids, list):
        ids = []
    return [int(i) for i in ids if str(i).isdigit()]


def _set_scan_ids_session(request, ids):
    request.session[SCAN_SESSION_KEY] = ids
    request.session.modified = True


def _usuario_pode_gerir_separacao(user):
    """Somente superuser pode inspecionar qualquer tarefa sem filtro por setor."""
    return bool(getattr(user, 'is_superuser', False))


def _setores_usuario_normalizados(usuario):
    if usuario is None or not usuario.setores.exists():
        return set()
    setores = list(usuario.setores.values_list('nome', flat=True))
    normalizados = set()
    for setor in setores:
        valor = (setor or '').strip().upper()
        if valor == 'FILTRO':
            valor = 'FILTROS'
        if valor:
            normalizados.add(valor)
    return normalizados


def _usuario_sem_setor_operacional(usuario):
    if _usuario_pode_gerir_separacao(usuario):
        return False
    return not _setores_usuario_normalizados(usuario)


def _obter_tarefa_permitida(request, tarefa_id):
    """
    Retorna a tarefa se o usuário pode acessá-la.

    Regras:
    - Tarefa ABERTA: qualquer separador/gestor da lista pode abrir.
    - Tarefa em execução (ou outro status com responsável): só o dono
      (usuario_em_execucao ou usuario) ou quem pode gerir (gestor/staff/superuser).
    - Sem responsável definido: mantém acesso (fluxo legado / liberação).
    """
    pode_gerir = _usuario_pode_gerir_separacao(request.user)
    base_qs = (
        Tarefa.objects.select_related('nf', 'rota', 'usuario', 'usuario_em_execucao')
        .prefetch_related('itens__produto')
    )
    if pode_gerir:
        tarefa = get_object_or_404(base_qs, id=tarefa_id)
    else:
        if _usuario_sem_setor_operacional(request.user):
            raise PermissionDenied('Usuário sem setor vinculado. Contate o administrador.')
        tarefa = get_object_or_404(base_qs.filter(ativo=True), id=tarefa_id)
        if not request.user.setores.filter(nome=tarefa.setor).exists():
            raise PermissionDenied('Usuário sem acesso ao setor')

    usuario_responsavel = (tarefa.usuario_em_execucao_id or tarefa.usuario_id) == request.user.id
    tarefa_sem_responsavel = (tarefa.usuario_em_execucao_id or tarefa.usuario_id) is None

    logger.info(
        'Acesso separacao_exec_web: tarefa_id=%s ativo=%s status=%s tarefa_usuario_id=%s request_user_id=%s '
        'perfil=%s superuser=%s is_staff=%s pode_gerir=%s',
        tarefa.id,
        tarefa.ativo,
        tarefa.status,
        tarefa.usuario_em_execucao_id or tarefa.usuario_id,
        request.user.id,
        getattr(request.user, 'perfil', None),
        getattr(request.user, 'is_superuser', False),
        getattr(request.user, 'is_staff', False),
        pode_gerir,
    )

    if (
        tarefa.status == Tarefa.Status.ABERTO
        or pode_gerir
        or usuario_responsavel
        or tarefa_sem_responsavel
    ):
        return tarefa

    logger.warning(
        'Acesso negado em separacao_exec_web: tarefa_id=%s status=%s bloqueada para user_id=%s (responsavel_id=%s)',
        tarefa.id,
        tarefa.status,
        request.user.id,
        tarefa.usuario_em_execucao_id or tarefa.usuario_id,
    )
    raise PermissionDenied(
        'Tarefa não disponível para o usuário: em execução por outro operador ou sem permissão de gestão.'
    )


def _obter_conferencia_contexto(nf_id, usuario):
    nf = get_object_or_404(
        NotaFiscal.objects.select_related('cliente', 'rota').prefetch_related(
            Prefetch('conferencias', queryset=Conferencia.objects.select_related('conferente').prefetch_related('itens__produto'))
        ),
        id=nf_id,
    )
    atualizar_status_nf(nf)
    setores_usuario = _setores_usuario_normalizados(usuario)
    conferencias_relacionadas = []
    for conferencia in nf.conferencias.all():
        if conferencia.status == Conferencia.Status.CANCELADA:
            continue
        setores_conferencia = {
            (item.produto.categoria or '').strip().upper()
            for item in conferencia.itens.all()
            if getattr(item, 'produto', None) is not None and (item.produto.categoria or '').strip()
        }
        if 'FILTRO' in setores_conferencia:
            setores_conferencia.discard('FILTRO')
            setores_conferencia.add('FILTROS')
        if not setores_usuario or setores_conferencia.intersection(setores_usuario):
            conferencias_relacionadas.append(conferencia)

    conferencia_ativa = next(
        (
            conferencia
            for conferencia in conferencias_relacionadas
            if conferencia.status == Conferencia.Status.EM_CONFERENCIA and conferencia.conferente_id == usuario.id
        ),
        None,
    )
    conferencia_recente = conferencia_ativa or next(iter(sorted(conferencias_relacionadas, key=lambda conferencia: conferencia.created_at, reverse=True)), None)
    return nf, conferencia_recente, conferencia_ativa


def _cabecalho_tarefa_separacao(tarefa):
    if tarefa.nf_id:
        return {
            'nf_numero': tarefa.nf.numero,
            'cliente_nome': tarefa.nf.cliente.nome if tarefa.nf.cliente_id else 'CONSOLIDADO',
        }

    nfs = []
    vistos = set()
    itens = getattr(tarefa, 'itens', None)
    itens = itens.select_related('nf', 'nf__cliente').all() if itens else []
    for item in itens:
        if not item.nf_id or item.nf_id in vistos:
            continue
        vistos.add(item.nf_id)
        nfs.append(item.nf)

    if len(nfs) == 1:
        nf = nfs[0]
        return {
            'nf_numero': nf.numero,
            'cliente_nome': nf.cliente.nome if nf.cliente_id else 'CONSOLIDADO',
        }
    if nfs:
        return {
            'nf_numero': f'{len(nfs)} NFs',
            'cliente_nome': 'CONSOLIDADO',
        }
    return {
        'nf_numero': '-',
        'cliente_nome': 'CONSOLIDADO',
    }


def _item_atual_separacao(itens_exibicao):
    if not itens_exibicao:
        return None
    for item in itens_exibicao:
        if item['status'] != 'SEPARADO':
            return item
    return itens_exibicao[0]


def _resumo_tarefa_separacao(itens_exibicao):
    total_itens = len(itens_exibicao)
    separados = sum(1 for item in itens_exibicao if item['status'] == 'SEPARADO')
    return {
        'total': total_itens,
        'separado': separados,
        'total_itens': total_itens,
        'separados': separados,
        'pendentes': max(total_itens - separados, 0),
    }


@require_profiles(Usuario.Perfil.GESTOR)
def importar_xml_web(request):
    resultados = None

    if request.method == 'POST':
        balcao = request.POST.get('balcao') in {'1', 'on', 'true', 'True'}
        tipo_entrada = EntradaNF.Tipo.BALCAO if balcao else EntradaNF.Tipo.NORMAL
        xml_files = request.FILES.getlist('xml_files')
        resultados = {
            'sucesso': 0,
            'duplicadas': 0,
            'erros': 0,
            'detalhes': [],
        }

        if not xml_files:
            messages.error(request, 'Selecione ao menos um arquivo XML para importação.')
        elif len(xml_files) > MAX_XML_FILES_POR_ENVIO:
            messages.error(
                request,
                f'Limite máximo de {MAX_XML_FILES_POR_ENVIO} arquivos por envio. '
                'Divida o lote e tente novamente.',
            )
        else:
            for inicio in range(0, len(xml_files), MAX_XML_FILES_POR_LOTE):
                lote = xml_files[inicio:inicio + MAX_XML_FILES_POR_LOTE]
                for xml_file in lote:
                    nome_arquivo = getattr(xml_file, 'name', 'arquivo_sem_nome')
                    if not nome_arquivo.lower().endswith('.xml'):
                        resultados['erros'] += 1
                        resultados['detalhes'].append(
                            _resultado_erro_importacao('Arquivo ignorado: extensão inválida.', arquivo=nome_arquivo)
                        )
                        continue

                    if getattr(xml_file, 'size', 0) == 0:
                        resultados['erros'] += 1
                        resultados['detalhes'].append(
                            _resultado_erro_importacao('Arquivo XML vazio.', arquivo=nome_arquivo)
                        )
                        continue

                    try:
                        resumo_nfe = extrair_resumo_nfe_xml(xml_file)
                        chave_nfe = resumo_nfe['chave_nfe']
                        numero_nf = resumo_nfe['numero_nf']
                        nota_existente = NotaFiscal.objects.filter(chave_nfe=chave_nfe).first()
                        status_inicial = EntradaNF.Status.PROCESSADO if nota_existente else EntradaNF.Status.AGUARDANDO
                        entrada, created = EntradaNF.objects.get_or_create(
                            chave_nf=chave_nfe,
                            defaults={
                                'numero_nf': numero_nf,
                                'xml': xml_file,
                                'status': status_inicial,
                                'tipo': tipo_entrada,
                            },
                        )
                        if created:
                            resultados['sucesso'] += 1
                            resultados['detalhes'].append(
                                {
                                    'status': 'sucesso',
                                    'mensagem': (
                                        'NF já existente no sistema. Entrada marcada como PROCESSADO.'
                                        if nota_existente
                                        else 'XML recebido. NF adicionada à fila de entradas.'
                                    ),
                                    'nf': numero_nf,
                                    'chave_nfe': chave_nfe,
                                    'arquivo': nome_arquivo,
                                }
                            )
                        else:
                            resultados['duplicadas'] += 1
                            resultados['detalhes'].append(
                                {
                                    'status': 'duplicada',
                                    'mensagem': 'Chave já cadastrada na fila de entradas.',
                                    'nf': entrada.numero_nf or '-',
                                    'chave_nfe': entrada.chave_nf,
                                    'arquivo': nome_arquivo,
                                }
                            )
                    except ImportacaoXMLError as exc:
                        resultados['erros'] += 1
                        resultados['detalhes'].append(
                            _resultado_erro_importacao(str(exc), arquivo=nome_arquivo)
                        )
                    except Exception as exc:
                        resultados['erros'] += 1
                        resultados['detalhes'].append(
                            _resultado_erro_importacao(str(exc), arquivo=nome_arquivo)
                        )

            if resultados['sucesso']:
                messages.success(request, f"XMLs recebidos na fila: {resultados['sucesso']}")
                messages.info(request, 'Nenhuma tarefa de separação foi criada automaticamente.')
            if resultados['duplicadas']:
                messages.warning(request, f"XMLs duplicados: {resultados['duplicadas']}")
            if resultados['erros']:
                messages.error(request, f"XMLs com erro: {resultados['erros']}")

    return _render(request, 'importar_xml.html', {'resultados': resultados})


@require_profiles(Usuario.Perfil.GESTOR)
def fila_entradas_nf_web(request):
    entradas = EntradaNF.objects.order_by('-data_importacao', '-id')
    pode_limpar = bool(getattr(request.user, 'is_superuser', False))
    paginacao = _paginar_lista(request, entradas)
    return _render(
        request,
        'fila_nfs_importadas.html',
        {
            'entradas': paginacao['page_obj'],
            'pode_limpar_dados': pode_limpar,
            **paginacao,
        },
    )


@require_profiles(Usuario.Perfil.GESTOR)
def limpar_dados_importacao_web(request):
    if request.method != 'POST':
        return redirect('web-fila-entradas-nf')

    if not getattr(request.user, 'is_superuser', False):
        messages.error(request, 'Somente administrador pode executar limpeza de dados.')
        return redirect('web-fila-entradas-nf')

    if request.POST.get('confirmar_limpeza') != 'SIM':
        messages.warning(request, 'Limpeza cancelada: confirmação obrigatória não informada.')
        return redirect('web-fila-entradas-nf')

    try:
        resultado = executar_limpeza_importacao_controlada()
    except LimpezaImportacaoError as exc:
        messages.error(request, str(exc))
        return redirect('web-fila-entradas-nf')
    except Exception:
        messages.error(request, 'Falha inesperada ao executar limpeza segura de dados.')
        return redirect('web-fila-entradas-nf')

    periodo = (
        f'{resultado.periodo_inicio.strftime("%d/%m/%Y %H:%M")} até '
        f'{resultado.periodo_fim.strftime("%d/%m/%Y %H:%M")}'
    )
    messages.success(
        request,
        (
            'Limpeza executada com sucesso. '
            f'Registros XML removidos: {resultado.registros_entrada_removidos}. '
            f'Notas removidas: {resultado.notas_removidas}. '
            f'Período removido: {periodo}.'
        ),
    )
    return redirect('web-fila-entradas-nf')


@require_profiles(Usuario.Perfil.GESTOR)
def ativacao_scan_nfs_web(request):
    entradas_ids = _scan_ids_session(request)
    entradas = list(EntradaNF.objects.filter(id__in=entradas_ids))
    entradas_map = {entrada.id: entrada for entrada in entradas}
    entradas_ordenadas = [entradas_map[i] for i in entradas_ids if i in entradas_map]
    return _render(request, 'ativacao_scan_nfs.html', {'entradas_scan': entradas_ordenadas})


@require_profiles(Usuario.Perfil.GESTOR)
def scan_nf_api(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'erro': 'Método não permitido.'}, status=405)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        payload = {}

    codigo = (payload.get('codigo') or request.POST.get('codigo') or '').strip()
    if not codigo:
        return JsonResponse({'ok': False, 'erro': 'Informe um código para leitura.'}, status=400)

    entrada = (
        EntradaNF.objects.filter(chave_nf=codigo).order_by('-id').first()
        or EntradaNF.objects.filter(numero_nf=codigo).order_by('-id').first()
    )

    if not entrada:
        nf_existente = (
            NotaFiscal.objects.filter(chave_nfe=codigo).first()
            or NotaFiscal.objects.filter(numero=codigo).order_by('-id').first()
        )
        if nf_existente:
            return JsonResponse(
                {
                    'ok': False,
                    'tipo_retorno': 'warning',
                    'erro': 'NF já processada e não pode ser liberada novamente.',
                    'detalhes': {
                        'numero_nf': nf_existente.numero,
                        'status_atual': nf_existente.status,
                        'data_ultimo_processamento': nf_existente.updated_at.strftime('%d/%m/%Y %H:%M:%S'),
                    },
                },
                status=409,
            )
        return JsonResponse(
            {
                'ok': False,
                'tipo_retorno': 'error',
                'erro': 'NF não encontrada na base de dados.',
            },
            status=404,
        )

    if entrada.status != EntradaNF.Status.AGUARDANDO:
        return JsonResponse(
            {
                'ok': False,
                'tipo_retorno': 'warning',
                'erro': 'NF já processada e não pode ser liberada novamente.',
                'detalhes': {
                    'numero_nf': entrada.numero_nf or '-',
                    'status_atual': entrada.status,
                    'data_ultimo_processamento': entrada.updated_at.strftime('%d/%m/%Y %H:%M:%S'),
                },
            },
            status=409,
        )

    ids = _scan_ids_session(request)
    if entrada.id in ids:
        return JsonResponse(
            {
                'ok': True,
                'duplicada': True,
                'mensagem': 'NF já escaneada nesta sessão.',
                'entrada': {
                    'id': entrada.id,
                    'numero_nf': entrada.numero_nf,
                    'chave_nf': entrada.chave_nf,
                    'status': entrada.status,
                    'data_importacao': entrada.data_importacao.strftime('%d/%m/%Y %H:%M:%S'),
                },
            }
        )

    ids.append(entrada.id)
    _set_scan_ids_session(request, ids)

    return JsonResponse(
        {
            'ok': True,
            'tipo_retorno': 'success',
            'mensagem': 'NF adicionada com sucesso.',
            'entrada': {
                'id': entrada.id,
                'numero_nf': entrada.numero_nf,
                'chave_nf': entrada.chave_nf,
                'status': entrada.status,
                'tipo': entrada.tipo,
                'data_importacao': entrada.data_importacao.strftime('%d/%m/%Y %H:%M:%S'),
            },
        }
    )


@require_profiles(Usuario.Perfil.GESTOR)
def remover_scan_nf_api(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'erro': 'Método não permitido.'}, status=405)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        payload = {}

    entrada_id = payload.get('entrada_id')
    if entrada_id is None:
        return JsonResponse({'ok': False, 'erro': 'entrada_id não informado.'}, status=400)

    ids = _scan_ids_session(request)
    try:
        entrada_id = int(entrada_id)
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'erro': 'entrada_id inválido.'}, status=400)

    if entrada_id not in ids:
        return JsonResponse({'ok': False, 'erro': 'NF não está na lista escaneada.'}, status=404)

    ids = [i for i in ids if i != entrada_id]
    _set_scan_ids_session(request, ids)
    return JsonResponse({'ok': True, 'mensagem': 'NF removida da lista de scan.'})


@require_profiles(Usuario.Perfil.GESTOR)
def confirmar_scan_entradas_web(request):
    if request.method != 'POST':
        return redirect('web-ativacao-scan-nf')

    ids = _scan_ids_session(request)
    if not ids:
        messages.warning(request, 'Nenhuma NF escaneada para confirmar.')
        return redirect('web-ativacao-scan-nf')

    entradas = list(EntradaNF.objects.filter(id__in=ids))
    entradas_ordenadas = []
    for entrada in entradas:
        data_emissao = None
        try:
            with open_entrada_xml(entrada, user=request.user) as arquivo_xml:
                documento = analisar_xml_nfe(arquivo_xml)
                data_emissao = documento.data_emissao
        except Exception:
            data_emissao = None
        entradas_ordenadas.append((entrada, data_emissao))

    entradas_ordenadas.sort(
        key=lambda item: item[1] if item[1] is not None else timezone.make_aware(datetime(1900, 1, 1)),
        reverse=True,
    )

    liberadas = 0
    bloqueadas = 0
    duplicadas = 0
    erros = 0
    for entrada, _data_emissao in entradas_ordenadas:
        if entrada.status != EntradaNF.Status.AGUARDANDO:
            continue
        try:
            with open_entrada_xml(entrada, user=request.user) as arquivo_xml:
                resultado = importar_xml_nfe(
                    arquivo_xml,
                    usuario=request.user,
                    balcao=entrada.tipo == EntradaNF.Tipo.BALCAO,
                    tarefas_lote_cache={},
                )
            status_resultado = resultado.get('status')
            if status_resultado == 'bloqueada':
                entrada.status = EntradaNF.Status.PROCESSADO
                bloqueadas += 1
            elif status_resultado == 'duplicada':
                entrada.status = EntradaNF.Status.PROCESSADO
                duplicadas += 1
            else:
                entrada.status = EntradaNF.Status.LIBERADO
                liberadas += 1
            entrada.save(update_fields=['status', 'updated_at'])
        except ImportacaoXMLError:
            entrada.status = EntradaNF.Status.PROCESSADO
            entrada.save(update_fields=['status', 'updated_at'])
            erros += 1
        except XMLStorageUnavailableError as exc:
            logger.error('Falha ao abrir XML da entrada %s durante confirmacao em lote: %s', entrada.id, str(exc))
            erros += 1
        except Exception:
            erros += 1

    _set_scan_ids_session(request, [])
    if liberadas:
        messages.success(request, f'{liberadas} NF(s) liberada(s) para separação.')
    if bloqueadas:
        messages.warning(request, f'{bloqueadas} NF(s) bloqueada(s) por cancelamento/denegação.')
    if duplicadas:
        messages.info(request, f'{duplicadas} NF(s) já existentes ignorada(s) como duplicadas.')
    if erros:
        messages.error(request, f'{erros} NF(s) com falha no processamento.')
    return redirect('web-ativacao-scan-nf')


@require_profiles(Usuario.Perfil.GESTOR)
def liberar_entrada_nf_web(request, entrada_id):
    if request.method != 'POST':
        return redirect('web-fila-entradas-nf')

    entrada = get_object_or_404(EntradaNF, id=entrada_id)
    if entrada.status == EntradaNF.Status.LIBERADO:
        messages.info(request, 'Entrada já liberada anteriormente.')
        return redirect('web-fila-entradas-nf')

    try:
        with open_entrada_xml(entrada, user=request.user) as arquivo_xml:
            resultado = importar_xml_nfe(
                arquivo_xml,
                usuario=request.user,
                balcao=entrada.tipo == EntradaNF.Tipo.BALCAO,
                tarefas_lote_cache={},
            )
        entrada.status = EntradaNF.Status.LIBERADO
        entrada.save(update_fields=['status', 'updated_at'])
        messages.success(
            request,
            f"Entrada {entrada.chave_nf} liberada com sucesso ({resultado.get('mensagem', 'processada')}).",
        )
    except XMLStorageUnavailableError as exc:
        nf_existente = NotaFiscal.objects.filter(chave_nfe=entrada.chave_nf).first()
        if nf_existente is not None:
            entrada.status = EntradaNF.Status.LIBERADO
            entrada.save(update_fields=['status', 'updated_at'])
            Log.objects.create(
                usuario=request.user,
                acao='LIBERACAO ENTRADA SEM XML',
                detalhe=(
                    f'Entrada {entrada.id} liberada sem reimportar XML ausente. '
                    f'NF {nf_existente.numero} ({nf_existente.chave_nfe}) ja existia no sistema.'
                ),
            )
            messages.warning(
                request,
                (
                    f'Entrada {entrada.chave_nf} liberada sem o arquivo XML, '
                    'usando a NF já existente no sistema.'
                ),
            )
        else:
            messages.error(request, f'XML indisponível para a entrada {entrada.chave_nf}: {str(exc)}')
    except ImportacaoXMLError as exc:
        messages.error(request, f'Falha ao liberar entrada {entrada.chave_nf}: {str(exc)}')
    except IntegrityError:
        entrada.status = EntradaNF.Status.PROCESSADO
        entrada.save(update_fields=['status', 'updated_at'])
        messages.warning(
            request,
            f'Entrada {entrada.chave_nf} já estava processada como NF no sistema.',
        )

    return redirect('web-fila-entradas-nf')


@require_profiles(Usuario.Perfil.SEPARADOR, Usuario.Perfil.GESTOR)
def separacao_lista_web(request):
    if _usuario_sem_setor_operacional(request.user):
        contexto = {'tarefas': [], 'is_paginated': False, 'pagination_query': ''}
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return _render(request, 'partials/separacao_lista_tabela.html', contexto)
        messages.error(request, 'Usuário sem setor vinculado. Contate o administrador.')
        return _render(request, 'separacao_lista.html', contexto)
    paginacao = _paginar_lista(request, listar_tarefas_disponiveis(request.user))
    contexto = {'tarefas': paginacao['page_obj'], **paginacao}
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return _render(request, 'partials/separacao_lista_tabela.html', contexto)
    return _render(request, 'separacao_lista.html', contexto)


@require_profiles(Usuario.Perfil.SEPARADOR, Usuario.Perfil.GESTOR)
def separacao_exec_web(request, tarefa_id):
    try:
        tarefa = _obter_tarefa_permitida(request, tarefa_id)
    except PermissionDenied as exc:
        logger.warning(
            'separacao_exec_web 403: tarefa_id=%s user_id=%s motivo=%s',
            tarefa_id,
            getattr(request.user, 'id', None),
            str(exc),
        )
        return HttpResponseForbidden(str(exc))

    if request.method == 'GET' and tarefa.status == Tarefa.Status.ABERTO:
        try:
            iniciar_tarefa(tarefa.id, request.user)
            return redirect('web-separacao-exec', tarefa_id=tarefa.id)
        except SeparacaoError as exc:
            messages.error(request, str(exc))
            return redirect('web-separacao-lista')

    if tarefa.status in {
        Tarefa.Status.CONCLUIDO,
        Tarefa.Status.CONCLUIDO_COM_RESTRICAO,
        Tarefa.Status.FECHADO_COM_RESTRICAO,
    }:
        messages.warning(request, 'Tarefa já finalizada e removida da fila operacional.')
        return redirect('web-separacao-lista')

    if request.method == 'POST':
        acao = request.POST.get('acao')
        try:
            if acao == 'iniciar':
                iniciar_tarefa(tarefa.id, request.user)
                messages.success(request, 'Tarefa aceita e em execução.')
            elif acao == 'bipar':
                codigo = (request.POST.get('codigo') or '').strip()
                if not codigo:
                    messages.error(request, 'Informe o código para bipagem.')
                else:
                    bipar_tarefa(tarefa.id, codigo, request.user)
                    messages.success(request, 'Bipagem registrada com sucesso.')
            elif acao == 'finalizar':
                status_final = request.POST.get('status_final') or Tarefa.Status.CONCLUIDO
                if status_final == OPERACIONAL_STATUS_BLOQUEADO:
                    messages.error(request, OPERACIONAL_STATUS_BLOQUEADO_ERRO)
                else:
                    finalizar_tarefa(tarefa.id, status_final, request.user, request.POST.get('motivo_restricao'))
                    messages.success(request, 'Tarefa finalizada.')
            elif acao == 'continuar_depois':
                liberar_execucao_tarefa(tarefa.id, request.user)
                messages.warning(request, 'Tarefa mantida para continuar depois.')
                return redirect('web-separacao-lista')
        except SeparacaoError as exc:
            messages.error(request, str(exc))
        except Exception as exc:
            logger.exception('Erro separacao POST: tarefa_id=%s user_id=%s erro=%s', tarefa_id, getattr(request.user, 'id', None), str(exc))
            raise
        return redirect('web-separacao-exec', tarefa_id=tarefa.id)

    try:
        tarefa = _obter_tarefa_permitida(request, tarefa_id)
    except PermissionDenied as exc:
        logger.warning(
            'separacao_exec_web GET 403: tarefa_id=%s user_id=%s motivo=%s',
            tarefa_id,
            getattr(request.user, 'id', None),
            str(exc),
        )
        return HttpResponseForbidden(str(exc))
    if tarefa.status in {
        Tarefa.Status.CONCLUIDO,
        Tarefa.Status.CONCLUIDO_COM_RESTRICAO,
        Tarefa.Status.FECHADO_COM_RESTRICAO,
    }:
        messages.warning(request, 'Tarefa já finalizada e removida da fila operacional.')
        return redirect('web-separacao-lista')
    try:
        itens_exibicao = listar_itens_tarefa_para_exibicao_seguro(tarefa)
        return _render(
            request,
            'separacao_exec.html',
            {
                'tarefa': tarefa,
                'itens_exibicao': itens_exibicao,
                'item_atual': _item_atual_separacao(itens_exibicao),
                'resumo_tarefa': _resumo_tarefa_separacao(itens_exibicao),
                'cabecalho_tarefa': _cabecalho_tarefa_separacao(tarefa),
                'status_finalizacao': [
	                Tarefa.Status.CONCLUIDO,
	                Tarefa.Status.CONCLUIDO_COM_RESTRICAO,
	                Tarefa.Status.FECHADO_COM_RESTRICAO,
                ],
            },
        )
    except Exception as exc:
        logger.exception('Erro separacao GET: tarefa_id=%s user_id=%s erro=%s', tarefa_id, getattr(request.user, 'id', None), str(exc))
        raise


@require_profiles(Usuario.Perfil.CONFERENTE, Usuario.Perfil.GESTOR)
def conferencia_lista_web(request):
    if _usuario_sem_setor_operacional(request.user):
        contexto = {'nfs': [], 'is_paginated': False, 'pagination_query': ''}
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return _render(request, 'partials/conferencia_lista_tabela.html', contexto)
        messages.error(request, 'Usuário sem setor vinculado. Contate o administrador.')
        return _render(request, 'conferencia_lista.html', contexto)
    paginacao = _paginar_lista(request, listar_nfs_disponiveis(request.user))
    contexto = {'nfs': paginacao['page_obj'], **paginacao}
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return _render(request, 'partials/conferencia_lista_tabela.html', contexto)
    return _render(request, 'conferencia_lista.html', contexto)


@require_profiles(Usuario.Perfil.CONFERENTE, Usuario.Perfil.GESTOR)
def conferencia_exec_web(request, nf_id):
    nf, conferencia, conferencia_ativa = _obter_conferencia_contexto(nf_id, request.user)

    if request.method == 'POST':
        acao = request.POST.get('acao')
        try:
            if acao == 'iniciar':
                iniciar_conferencia(nf.id, request.user)
                messages.success(request, 'Conferência iniciada.')
            elif acao == 'bipar':
                if not conferencia_ativa:
                    messages.error(request, 'Inicie a conferência antes de bipar itens.')
                else:
                    codigo = (request.POST.get('codigo') or '').strip()
                    if not codigo:
                        messages.error(request, 'Informe o código para bipagem.')
                    else:
                        bipar_conferencia(conferencia_ativa.id, codigo, request.user)
                        messages.success(request, 'Bipagem de conferência registrada.')
            elif acao == 'finalizar':
                if not conferencia_ativa:
                    messages.error(request, 'Nenhuma conferência em andamento para finalizar.')
                else:
                    finalizar_conferencia(conferencia_ativa.id, request.user)
                    messages.success(request, 'Conferência finalizada.')
        except ConferenciaError as exc:
            messages.error(request, str(exc))
        return redirect('web-conferencia-exec', nf_id=nf.id)

    nf, conferencia, conferencia_ativa = _obter_conferencia_contexto(nf_id, request.user)
    return _render(
        request,
        'conferencia_exec.html',
        {
            'nf': nf,
            'conferencia': conferencia,
            'conferencia_ativa': conferencia_ativa,
        },
    )


@require_profiles(Usuario.Perfil.GESTOR)
def clientes_web(request):
    if request.method == 'POST':
        acao = request.POST.get('acao') or 'cadastrar'
        if acao == 'upload' and request.FILES.get('arquivo'):
            arquivo = request.FILES['arquivo']
            try:
                resultado = importar_clientes_arquivo(arquivo)
                messages.success(
                    request,
                    (
                        'Upload de clientes concluido. '
                        f"Criados: {resultado.get('criados', 0)} | "
                        f"Atualizados: {resultado.get('atualizados', 0)} | "
                        f"Ignorados: {resultado.get('ignorados', 0)}."
                    ),
                )
            except Exception as exc:
                messages.error(request, f'Erro ao importar arquivo de clientes: {str(exc)}')
        else:
            nome = (request.POST.get('nome') or '').strip()
            inscricao = (request.POST.get('inscricao_estadual') or '').strip()
            cliente = Cliente(
                codigo=(request.POST.get('codigo') or '').strip() or None,
                nome=nome,
                rota=(request.POST.get('rota') or '').strip() or None,
                inscricao_estadual=inscricao or f'SEM-IE-{nome[:35]}',
                ativo=True,
            )
            try:
                cliente.full_clean()
                cliente.save()
                messages.success(request, 'Cliente cadastrado com sucesso.')
            except ValidationError as exc:
                messages.error(request, '; '.join(exc.messages))
        return redirect('web-clientes')

    return _render(request, 'clientes.html', {'clientes': Cliente.objects.order_by('nome')})


@require_profiles(Usuario.Perfil.GESTOR)
def produtos_web(request):
    if request.method == 'POST':
        acao = request.POST.get('acao') or 'cadastrar'
        if acao == 'upload' and request.FILES.get('arquivo'):
            arquivo = request.FILES['arquivo']
            try:
                resultado = importar_produtos_arquivo(arquivo)
                messages.success(
                    request,
                    (
                        'Upload de produtos concluido. '
                        f"Linhas: {resultado.get('total_linhas', 0)} | "
                        f"Processados: {resultado.get('total_processado', 0)} | "
                        f"Criados: {resultado['criados']} | "
                        f"Atualizados: {resultado['atualizados']} | "
                        f"Ignorados: {resultado['ignorados']}."
                    ),
                )
                if resultado.get('ignorado_por_motivo'):
                    detalhes = ', '.join(
                        f'{motivo}: {quantidade}'
                        for motivo, quantidade in resultado['ignorado_por_motivo'].items()
                    )
                    messages.warning(request, f'Linhas ignoradas por motivo -> {detalhes}')
            except Exception as exc:
                messages.error(request, f'Erro ao importar arquivo de produtos: {str(exc)}')
        else:
            produto_id = (request.POST.get('produto_id') or '').strip()
            if produto_id:
                produto = Produto.objects.filter(id=produto_id).first()
                if not produto:
                    messages.error(request, 'Produto selecionado para edicao nao foi encontrado.')
                    return redirect('web-produtos')
                mensagem_sucesso = 'Produto atualizado com sucesso.'
            else:
                produto = Produto(ativo=True, cadastrado_manual=True)
                mensagem_sucesso = 'Produto cadastrado com sucesso.'

            produto.cod_prod = _normalizar_codigo_leitura(request.POST.get('cod_prod')) or ''
            produto.codigo = _normalizar_codigo_leitura(request.POST.get('codigo'))
            produto.descricao = (request.POST.get('descricao') or '').strip()
            produto.embalagem = (request.POST.get('embalagem') or '').strip() or None
            produto.cod_ean = _normalizar_codigo_leitura(request.POST.get('ean') or request.POST.get('cod_ean'))
            produto.setor = _normalizar_setor_produto(request.POST.get('setor'))
            if not produto.setor:
                messages.error(request, 'Setor e obrigatorio.')
                return redirect('web-produtos')
            produto.unidade = produto.embalagem
            produto.categoria = _categoria_por_setor_produto(produto.setor)
            produto.incompleto = False
            try:
                produto.full_clean()
                produto.save()
                _vincular_grupo_agregado_produto(produto)
                sync_result = sincronizar_referencias_produto(produto)
                if sync_result['itens_tarefa_corrigidos'] or sync_result['itens_conferencia_corrigidos']:
                    messages.info(
                        request,
                        (
                            'Sincronização aplicada: '
                            f"{sync_result['itens_tarefa_corrigidos']} item(ns) de separação e "
                            f"{sync_result['itens_conferencia_corrigidos']} item(ns) de conferência atualizados."
                        ),
                    )
                messages.success(request, mensagem_sucesso)
            except ValidationError as exc:
                messages.error(request, '; '.join(exc.messages))
        return redirect('web-produtos')

    busca = (request.GET.get('q') or '').strip()
    apenas_incompletos = request.GET.get('incompletos') in {'1', 'true', 'on'}
    produtos_qs = Produto.objects.order_by('cod_prod')
    if busca:
        produtos_qs = produtos_qs.filter(
            Q(descricao__icontains=busca) |
            Q(cod_prod__icontains=busca) |
            Q(codigo__icontains=busca) |
            Q(cod_ean__icontains=busca) |
            Q(setor__icontains=busca)
        )
    if apenas_incompletos:
        produtos_qs = produtos_qs.filter(incompleto=True)
    paginador = Paginator(produtos_qs, 20)
    produtos_page = paginador.get_page(request.GET.get('page'))

    return _render(
        request,
        'produtos.html',
        {
            'produtos': produtos_page,
            'busca': busca,
            'apenas_incompletos': apenas_incompletos,
            'setores_produto': (
                ('FILTRO', 'Filtro'),
                ('LUBRIFICANTE', 'Lubrificante'),
                ('AGREGADO', 'Agregado'),
                ('NAO ENCONTRADO', 'Nao encontrado'),
            ),
        },
    )


@require_profiles(Usuario.Perfil.GESTOR)
def rotas_web(request):
    if request.method == 'POST':
        acao = request.POST.get('acao') or 'cadastrar'
        if acao == 'upload' and request.FILES.get('arquivo'):
            arquivo = request.FILES['arquivo']
            try:
                resultado = importar_rotas_arquivo(arquivo)
                messages.success(
                    request,
                    (
                        'Upload de rotas concluido. '
                        f"Linhas: {resultado.get('total_linhas', 0)} | "
                        f"Processadas: {resultado.get('total_processado', 0)} | "
                        f"Criadas: {resultado.get('criados', 0)} | "
                        f"Atualizadas: {resultado.get('atualizados', 0)} | "
                        f"Ignoradas: {resultado.get('ignorados', 0)}."
                    ),
                )
            except Exception as exc:
                messages.error(request, f'Erro ao importar arquivo de rotas: {str(exc)}')
        else:
            rota = Rota(
                nome=(request.POST.get('nome') or '').strip(),
                cep_inicial=_normalizar_campo(request.POST.get('cep_inicial')),
                cep_final=_normalizar_campo(request.POST.get('cep_final')),
                bairro=_normalizar_campo(request.POST.get('bairro')),
            )
            try:
                rota.full_clean()
                rota.save()
                messages.success(request, 'Rota cadastrada com sucesso.')
            except ValidationError as exc:
                messages.error(request, '; '.join(exc.messages))
        return redirect('web-rotas')

    busca = (request.GET.get('q') or '').strip()
    rotas_qs = Rota.objects.order_by('nome')
    if busca:
        rotas_qs = rotas_qs.filter(Q(nome__icontains=busca) | Q(bairro__icontains=busca))
    paginador = Paginator(rotas_qs, 20)
    rotas_page = paginador.get_page(request.GET.get('page'))

    return _render(request, 'rotas.html', {'rotas': rotas_page, 'busca': busca})


@require_profiles(Usuario.Perfil.GESTOR)
def usuarios_web(request):
    Setor.garantir_setores_padrao()
    setores_disponiveis = list(Setor.objects.order_by('nome'))

    if request.method == 'POST':
        form = UsuarioForm(request.POST)
        try:
            if not form.is_valid():
                messages.error(request, '; '.join([f'{k}: {" ".join(v)}' for k, v in form.errors.items()]))
                return redirect('web-usuarios')
            form.save()
            messages.success(request, 'Usuário cadastrado com sucesso.')
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect('web-usuarios')

    form = UsuarioForm()
    paginacao = _paginar_lista(request, Usuario.objects.filter(is_active=True).order_by('nome'))
    return _render(
        request,
        'usuarios.html',
        {
            'usuarios': paginacao['page_obj'],
            'perfis': Usuario.Perfil.choices,
            'setores': setores_disponiveis,
            'form': form,
            **paginacao,
        },
    )


@require_profiles(Usuario.Perfil.GESTOR)
def toggle_usuario_status(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'erro': 'Método não permitido.'}, status=405)

    alvo = get_object_or_404(Usuario, id=user_id)
    if alvo.id == request.user.id:
        return JsonResponse({'ok': False, 'erro': 'Você não pode bloquear o próprio usuário.'}, status=400)
    if alvo.is_superuser or alvo.username.lower() == 'admin':
        return JsonResponse({'ok': False, 'erro': 'Não é permitido bloquear o usuário administrador principal.'}, status=400)

    alvo.is_active = not alvo.is_active
    alvo.save(update_fields=['is_active', 'updated_at'])
    acao = 'desbloqueado' if alvo.is_active else 'bloqueado'

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse(
            {
                'ok': True,
                'user_id': alvo.id,
                'is_active': alvo.is_active,
                'mensagem': f'Usuário {acao} com sucesso.',
            }
        )

    messages.success(request, f'Usuário {acao} com sucesso.')
    return redirect('web-usuarios')


@require_profiles(Usuario.Perfil.GESTOR)
def editar_usuario_web(request, user_id):
    usuario = get_object_or_404(Usuario, id=user_id)
    Setor.garantir_setores_padrao()
    setores = list(Setor.objects.order_by('nome'))

    if request.method == 'POST':
        usuario.nome = (request.POST.get('nome') or '').strip()
        usuario.username = (request.POST.get('username') or '').strip()
        usuario.perfil = request.POST.get('perfil') or Usuario.Perfil.SEPARADOR
        usuario.is_active = _bool_post(request, 'is_active', default=True)
        usuario.is_staff = _bool_post(request, 'is_staff', default=False)

        senha = (request.POST.get('senha') or '').strip()
        if senha:
            usuario.set_password(senha)

        setores_ids = request.POST.getlist('setores')
        setores_map = {str(setor.id): setor for setor in Setor.objects.filter(id__in=setores_ids)}
        setores_selecionados = [setores_map[setor_id] for setor_id in setores_ids if setor_id in setores_map]

        if not setores_selecionados:
            setor_padrao, _ = Setor.objects.get_or_create(nome=Setor.Codigo.NAO_ENCONTRADO)
            setores_selecionados = [setor_padrao]

        usuario.setor = setores_selecionados[0].nome

        try:
            usuario.full_clean()
            usuario.save()
            usuario.setores.set(setores_selecionados)
            messages.success(request, 'Usuário atualizado com sucesso.')
            return redirect('web-usuarios')
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))

    setores_usuario = set(usuario.setores.values_list('id', flat=True))
    if not setores_usuario and usuario.setor:
        setor_padrao = Setor.objects.filter(nome=usuario.setor).first()
        if setor_padrao:
            setores_usuario = {setor_padrao.id}

    return _render(
        request,
        'usuarios_editar.html',
        {
            'alvo': usuario,
            'perfis': Usuario.Perfil.choices,
            'setores': setores,
            'setores_usuario': setores_usuario,
        },
    )


@require_profiles(Usuario.Perfil.GESTOR)
def excluir_usuario_web(request, user_id):
    if request.method != 'POST':
        return redirect('web-usuarios')

    usuario = get_object_or_404(Usuario, id=user_id)
    if usuario.is_superuser or usuario.username.lower() == 'admin':
        messages.warning(request, 'Não é permitido excluir o administrador principal.')
        return redirect('web-usuarios')
    if usuario.id == request.user.id:
        messages.warning(request, 'Você não pode excluir o usuário logado.')
        return redirect('web-usuarios')

    usuario.is_active = False
    usuario.save(update_fields=['is_active', 'updated_at'])
    messages.success(request, 'Usuário desativado com sucesso.')
    return redirect('web-usuarios')