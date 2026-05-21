from datetime import date
from decimal import Decimal
from importlib import import_module
import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.db import OperationalError, connection, transaction
from django.db.models import F, Prefetch, Q
from django.utils import timezone

from apps.conferencia.models import Conferencia, ConferenciaItem
from apps.core.services.produto_validacao_service import (
    filtrar_queryset_por_codigo_produto,
    ProdutoValidacaoError,
    selecionar_item_por_codigo_lido,
    validar_produto,
)
from apps.logs.models import Log, UserActivityLog
from apps.nf.models import NotaFiscal, NotaFiscalItem
from apps.nf.services.consistencia_service import _itens_separacao_prefetch_nf, sanear_consistencia_nf, separacao_concluida_nf
from apps.nf.services.status_service import atualizar_status_nf, sincronizar_status_operacional_nf
from apps.produtos.models import Produto
from apps.tarefas.models import Tarefa, TarefaItem
from apps.tarefas.services.onda_fallback import obter_tarefa_separacao_com_fallback_onda
from apps.tarefas.services.onda_service import (
    normalizar_tipo_embalagem,
    registrar_item_tarefa_onda,
)
from apps.usuarios.models import Setor
from apps.usuarios.session_utils import usuario_esta_logado

logger = logging.getLogger(__name__)

CONFERENCIA_FINALIZACAO_LENTA_MS = 150
CONFERENCIA_LISTAGEM_WARNING_MS = 1000
CONFERENCIA_BIPAGEM_WARNING_MS = 1000
CONFERENCIA_FINALIZACAO_WARNING_MS = 1000
CONFERENCIA_LISTAGEM_MAX_RESULTADOS = 30
CONFERENCIA_LISTAGEM_JANELA_CANDIDATOS = 120


class ConferenciaError(Exception):
    pass


NF_CANCELADA_ERRO = 'NF cancelada não pode ser processada'
TAREFA_SETOR_ERRO = 'NF não pertence ao setor do usuário'
USUARIO_SEM_SETOR_ERRO = 'Usuário sem setor vinculado. Contate o administrador.'


STATUS_TAREFA_LIBERA_CONFERENCIA = {
    Tarefa.Status.CONCLUIDO,
    Tarefa.Status.LIBERADO_COM_RESTRICAO,
    Tarefa.Status.CONCLUIDO_COM_RESTRICAO,
}

STATUS_CONFERENCIA_EM_FLUXO = {
    Conferencia.Status.AGUARDANDO,
    Conferencia.Status.EM_CONFERENCIA,
    Conferencia.Status.LIBERADO_COM_RESTRICAO,
}

STATUS_CONFERENCIA_FINALIZADA = {
    Conferencia.Status.OK,
    Conferencia.Status.DIVERGENCIA,
    Conferencia.Status.CONCLUIDO_COM_RESTRICAO,
}

STATUS_CONFERENCIA_RESERVA_ITENS = STATUS_CONFERENCIA_EM_FLUXO | STATUS_CONFERENCIA_FINALIZADA
CONFERENCIA_LIST_CACHE_TTL = 15
CACHE_VERSION_KEY_NFS_DISPONIVEIS = 'conferencia:nfs:version'


def _normalizar_setor_operacional(valor):
    setor = (valor or '').strip().upper()
    if setor == 'FILTRO':
        return Setor.Codigo.FILTROS
    if setor == 'NAO ENCONTRADO':
        return Setor.Codigo.NAO_ENCONTRADO
    return setor


def _setor_operacional_produto(produto):
    setor = _normalizar_setor_operacional(getattr(produto, 'setor', None))
    if setor:
        return setor
    return _normalizar_setor_operacional(getattr(produto, 'categoria', None)) or Setor.Codigo.NAO_ENCONTRADO


def _usuario_pode_ver_todos_setores(usuario):
    return bool(getattr(usuario, 'is_superuser', False))


def _setores_usuario(usuario):
    from apps.core.operacional_cache import setores_usuario_operacional

    if usuario is None:
        return set()
    setores = setores_usuario_operacional(usuario)
    if setores is None:
        return set()
    if setores:
        return setores
    if getattr(usuario, 'setor', None) and usuario.setor != Setor.Codigo.NAO_ENCONTRADO:
        return {_normalizar_setor_operacional(usuario.setor)}
    return set()


def _nf_pertence_a_setores_usuario(nf, usuario):
    if usuario is None or _usuario_pode_ver_todos_setores(usuario):
        return True
    setores_usuario = _setores_usuario(usuario)
    if not setores_usuario:
        return False
    setores_nf = {
        _setor_item_nf(item_nf)
        for item_nf in _itens_nf_relacionados(nf)
        if _setor_item_nf(item_nf)
    }
    if not setores_nf:
        setores_nf = {
            _normalizar_setor_operacional(tarefa.setor)
            for tarefa in _tarefas_relacionadas_nf(nf)
            if _normalizar_setor_operacional(tarefa.setor)
        }
    if not setores_nf:
        return False
    return bool(setores_nf.intersection(setores_usuario))


def _cache_key_nfs_disponiveis(usuario):
	if usuario is None:
		return f'conferencia:nfs:v{_cache_version_nfs_disponiveis()}:anon'
	return f'conferencia:nfs:v{_cache_version_nfs_disponiveis()}:{usuario.id}'


def _cache_version_nfs_disponiveis():
    return int(cache.get(CACHE_VERSION_KEY_NFS_DISPONIVEIS, 1) or 1)


def invalidate_nfs_disponiveis_cache(*, motivo='', nf_id=None, setor=None):
    nova_versao = _cache_version_nfs_disponiveis() + 1
    cache.set(CACHE_VERSION_KEY_NFS_DISPONIVEIS, nova_versao, None)
    if motivo or nf_id or setor:
        logger.info(
            'INVALIDANDO_FILA_CONFERENCIA motivo=%s nf_id=%s setor=%s versao=%s',
            motivo or '',
            nf_id,
            setor or '',
            nova_versao,
        )


def _invalidate_conferencia_operacional_cache(*, motivo='', nf_id=None, setor=None):
    invalidate_nfs_disponiveis_cache(motivo=motivo, nf_id=nf_id, setor=setor)

    def _invalidar_monitoramento():
        from apps.core.services.visibilidade_operacional_service import invalidate_monitoramento_conferencia_cache

        invalidate_monitoramento_conferencia_cache(motivo=motivo, nf_id=nf_id, setor=setor)

    transaction.on_commit(_invalidar_monitoramento)


def _validar_setor_nf(nf, usuario):
    if usuario is None or _usuario_pode_ver_todos_setores(usuario):
        return
    setores_usuario = _setores_usuario(usuario)
    if not setores_usuario:
        raise ConferenciaError(USUARIO_SEM_SETOR_ERRO)
    if not _nf_pertence_a_setores_usuario(nf, usuario):
        raise ConferenciaError(TAREFA_SETOR_ERRO)


def _itens_separacao_nf_qs(nf):
    return TarefaItem.objects.filter(Q(tarefa__nf=nf) | Q(nf=nf))


def _itens_nf_relacionados(nf):
    itens = getattr(nf, '_prefetched_objects_cache', {}).get('itens')
    if itens is not None:
        return itens
    return nf.itens.select_related('produto').all()


def _tarefas_nf_relacionadas(nf):
    tarefas = getattr(nf, '_prefetched_objects_cache', {}).get('tarefas')
    if tarefas is not None:
        return tarefas
    return nf.tarefas.all()


def _conferencias_nf_relacionadas(nf):
    conferencias = getattr(nf, '_prefetched_objects_cache', {}).get('conferencias')
    if conferencias is not None:
        return conferencias
    return nf.conferencias.exclude(status=Conferencia.Status.CANCELADA).select_related('conferente').prefetch_related('itens')


def _setor_item_nf(item_nf):
    return _setor_operacional_produto(getattr(item_nf, 'produto', None))


def _itens_nf_por_usuario(nf, usuario):
    itens_nf = [item_nf for item_nf in _itens_nf_relacionados(nf) if item_nf.produto_id is not None]
    if usuario is None or _usuario_pode_ver_todos_setores(usuario):
        return itens_nf
    setores_usuario = _setores_usuario(usuario)
    return [item_nf for item_nf in itens_nf if _setor_item_nf(item_nf) in setores_usuario]


def _produto_ids_conferencia(conferencia):
    itens = getattr(conferencia, '_prefetched_objects_cache', {}).get('itens')
    if itens is None:
        itens = conferencia.itens.all()
    return {item.produto_id for item in itens if item.produto_id is not None}


def _conferencia_cobre_produtos(conferencia, produto_ids):
    if not produto_ids:
        return False
    return bool(_produto_ids_conferencia(conferencia).intersection(produto_ids))


def _conferencias_relacionadas_ao_usuario(nf, usuario, conferencias=None, produto_ids_usuario=None):
    if produto_ids_usuario is None:
        itens_usuario = _itens_nf_por_usuario(nf, usuario)
        produto_ids_usuario = {item_nf.produto_id for item_nf in itens_usuario}
    if conferencias is None:
        conferencias = _conferencias_nf_relacionadas(nf)
    return [
        conferencia
        for conferencia in conferencias
        if conferencia.status != Conferencia.Status.CANCELADA and _conferencia_cobre_produtos(conferencia, produto_ids_usuario)
    ]


def _produto_ids_reservados(conferencias, statuses=None):
    produto_ids = set()
    for conferencia in conferencias:
        if statuses is not None and conferencia.status not in statuses:
            continue
        produto_ids.update(_produto_ids_conferencia(conferencia))
    return produto_ids


def _conferencia_ativa_do_usuario(conferencias, usuario):
    for conferencia in conferencias:
        if conferencia.status == Conferencia.Status.EM_CONFERENCIA and conferencia.conferente_id == getattr(usuario, 'id', None):
            return conferencia
    return None


def _conferencia_ativa_outro_usuario(conferencias, usuario):
    for conferencia in conferencias:
        if conferencia.status == Conferencia.Status.EM_CONFERENCIA and conferencia.conferente_id != getattr(usuario, 'id', None):
            return conferencia
    return None


def _conferencia_mais_recente(conferencias):
    if not conferencias:
        return None
    return max(conferencias, key=lambda conferencia: (conferencia.created_at, conferencia.id))


def _resumo_separacao_nf(nf):
    itens_prefetch = _itens_separacao_prefetch_nf(nf)
    if itens_prefetch is not None:
        total_itens = len(itens_prefetch)
        if total_itens == 0:
            return {'status_separacao': 'PENDENTE', 'itens_pendentes': 0, 'itens_separados': 0, 'total_itens': 0, 'separado_em': None}
        itens_pendentes = sum(1 for item in itens_prefetch if item.quantidade_separada < item.quantidade_total)
        itens_separados = max(total_itens - itens_pendentes, 0)
        status_separacao = 'SEPARADO' if itens_pendentes == 0 else ('PARCIALMENTE_SEPARADA' if itens_separados > 0 else 'PENDENTE')
        datas_bipagem = [getattr(item, 'data_bipagem', None) for item in itens_prefetch if getattr(item, 'data_bipagem', None)]
        return {
            'status_separacao': status_separacao,
            'itens_pendentes': itens_pendentes,
            'itens_separados': itens_separados,
            'total_itens': total_itens,
            'separado_em': max(datas_bipagem) if datas_bipagem else None,
        }

    itens_qs = _itens_separacao_nf_qs(nf)
    itens_sql = str(itens_qs.query)
    itens_db = list(
        itens_qs.values(
            'id',
            'quantidade_total',
            'quantidade_separada',
            'data_bipagem',
        )
    )
    total_itens = len(itens_db)
    if total_itens == 0:
        logger.info(
            'CONFERENCIA_LIBERACAO_QUERY nf_id=%s origem=db itens_ids=%s quantidades=%s sql=%s',
            getattr(nf, 'id', None),
            [],
            [],
            itens_sql,
        )
        return {'status_separacao': 'PENDENTE', 'itens_pendentes': 0, 'itens_separados': 0, 'total_itens': 0, 'separado_em': None}

    itens_pendentes = sum(1 for item in itens_db if item['quantidade_separada'] < item['quantidade_total'])
    itens_separados = max(total_itens - itens_pendentes, 0)
    status_separacao = 'SEPARADO' if itens_pendentes == 0 else ('PARCIALMENTE_SEPARADA' if itens_separados > 0 else 'PENDENTE')
    datas_bipagem = [item['data_bipagem'] for item in itens_db if item['data_bipagem']]
    logger.info(
        'CONFERENCIA_LIBERACAO_QUERY nf_id=%s origem=db itens_ids=%s quantidades=%s sql=%s',
        getattr(nf, 'id', None),
        [item['id'] for item in itens_db],
        [
            {
                'id': item['id'],
                'quantidade_total': str(item['quantidade_total']),
                'quantidade_separada': str(item['quantidade_separada']),
            }
            for item in itens_db
        ],
        itens_sql,
    )
    return {
        'status_separacao': status_separacao,
        'itens_pendentes': itens_pendentes,
        'itens_separados': itens_separados,
        'total_itens': total_itens,
        'separado_em': max(datas_bipagem) if datas_bipagem else None,
    }


def _separado_em_nf(nf):
    return _resumo_separacao_nf(nf).get('separado_em')


def pedido_esta_liberado_para_conferencia(nf):
    resumo = _resumo_separacao_nf(nf)
    separado_em = resumo.get('separado_em')
    liberado = resumo['status_separacao'] == 'SEPARADO'
    resultado = {
        'liberado': liberado,
        'motivo': '' if liberado else 'Pedido ainda não foi separado',
        'status_fluxo': 'AGUARDANDO' if liberado else 'BLOQUEADO',
        'status_separacao': resumo['status_separacao'],
        'separado_em': separado_em.isoformat() if separado_em else None,
        'itens_pendentes': resumo['itens_pendentes'],
        'total_itens': resumo['total_itens'],
        'itens_separados': resumo['itens_separados'],
    }
    logger.info(
        'CONFERENCIA_LIBERACAO_DEBUG nf_id=%s nf_numero=%s status_separacao=%s separado_em=%s itens_separados=%s itens_pendentes=%s total_itens=%s liberado=%s motivo=%s',
        getattr(nf, 'id', None),
        getattr(nf, 'numero', ''),
        resultado['status_separacao'],
        resultado['separado_em'],
        resultado['itens_separados'],
        resultado['itens_pendentes'],
        resultado['total_itens'],
        resultado['liberado'],
        resultado['motivo'],
    )
    return resultado


def avaliar_liberacao_conferencia(nf):
    return pedido_esta_liberado_para_conferencia(nf)


def listar_nfs_disponiveis(
    usuario=None,
    *,
    somente_leitura=False,
    usar_cache=True,
    data_inicio=None,
    data_fim=None,
    max_resultados=None,
):
    inicio_listagem = time.perf_counter()
    cache_hit = False
    if connection.in_atomic_block:
        usar_cache = False
    if usuario is not None and not _usuario_pode_ver_todos_setores(usuario) and not _setores_usuario(usuario):
        return []
    if usar_cache:
        cache_key = _cache_key_nfs_disponiveis(usuario)
        cached = cache.get(cache_key)
        if cached is not None:
            cache_hit = True
            if data_inicio is None and data_fim is None:
                resultado_cache = cached[:max_resultados] if max_resultados else cached
                total_ms = round((time.perf_counter() - inicio_listagem) * 1000, 2)
                _log_conferencia_listagem(total_ms, usuario=usuario, total=len(resultado_cache), cache_hit=cache_hit)
                return resultado_cache
            filtrado_cache = _filtrar_nfs_por_periodo_lista(cached, data_inicio, data_fim)
            resultado_cache = filtrado_cache[:max_resultados] if max_resultados else filtrado_cache
            total_ms = round((time.perf_counter() - inicio_listagem) * 1000, 2)
            _log_conferencia_listagem(total_ms, usuario=usuario, total=len(resultado_cache), cache_hit=cache_hit)
            return resultado_cache
    tarefa_itens_prefetch = Prefetch(
        'itens',
        queryset=TarefaItem.objects.select_related('tarefa').only(
            'id',
            'tarefa_id',
            'nf_id',
            'quantidade_total',
            'quantidade_separada',
            'possui_restricao',
            'data_bipagem',
            'tarefa__id',
            'tarefa__status',
        ),
    )
    tarefas_prefetch = Prefetch(
        'tarefas',
        queryset=Tarefa.objects.only('id', 'nf_id', 'rota_id', 'tipo', 'setor').prefetch_related(tarefa_itens_prefetch).order_by('id'),
    )
    itens_prefetch = Prefetch(
        'itens',
        queryset=NotaFiscalItem.objects.select_related('produto').only(
            'id',
            'nf_id',
            'produto_id',
            'quantidade',
            'produto__id',
            'produto__categoria',
            'produto__setor',
        ),
    )
    itens_tarefa_prefetch = Prefetch(
        'itens_tarefa',
        queryset=TarefaItem.objects.select_related('tarefa').only(
            'id',
            'tarefa_id',
            'nf_id',
            'quantidade_total',
            'quantidade_separada',
            'possui_restricao',
            'data_bipagem',
            'tarefa__id',
            'tarefa__status',
        ),
    )
    conferencias_prefetch = Prefetch(
        'conferencias',
        queryset=Conferencia.objects.exclude(status=Conferencia.Status.CANCELADA)
        .select_related('conferente')
        .prefetch_related(
            Prefetch(
                'itens',
                queryset=ConferenciaItem.objects.only(
                    'id',
                    'conferencia_id',
                    'produto_id',
                    'status',
                    'qtd_esperada',
                    'qtd_conferida',
                ),
            )
        )
        .only(
            'id',
            'nf_id',
            'conferente_id',
            'status',
            'created_at',
            'updated_at',
            'conferente__id',
            'conferente__nome',
            'conferente__username',
        )
        .order_by('-created_at'),
    )
    nfs = (
        NotaFiscal.objects.select_related('cliente', 'rota')
        .defer('bairro')
        .prefetch_related(tarefas_prefetch, itens_prefetch, itens_tarefa_prefetch, conferencias_prefetch)
        .filter(status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA, ativa=True)
        .order_by('-data_emissao')
    )
    if data_inicio is not None:
        nfs = nfs.filter(
            Q(created_at__date__gte=data_inicio)
            | Q(updated_at__date__gte=data_inicio)
            | Q(data_emissao__date__gte=data_inicio)
        )
    if data_fim is not None:
        nfs = nfs.filter(
            Q(created_at__date__lte=data_fim)
            | Q(updated_at__date__lte=data_fim)
            | Q(data_emissao__date__lte=data_fim)
        )

    janela_candidatos = max(max_resultados or CONFERENCIA_LISTAGEM_MAX_RESULTADOS, CONFERENCIA_LISTAGEM_MAX_RESULTADOS)
    janela_candidatos = max(janela_candidatos * 4, CONFERENCIA_LISTAGEM_JANELA_CANDIDATOS)
    nfs = nfs[:janela_candidatos]

    disponiveis = []
    for nf in nfs:
        if not somente_leitura:
            consistencia = sanear_consistencia_nf(nf, actor=usuario)
            if not consistencia['valida']:
                continue

        validacao_fluxo = pedido_esta_liberado_para_conferencia(nf)
        if not _nf_pertence_a_setores_usuario(nf, usuario):
            continue

        itens_usuario = _itens_nf_por_usuario(nf, usuario)
        produto_ids_usuario = {item_nf.produto_id for item_nf in itens_usuario}
        if not produto_ids_usuario:
            continue

        conferencias_relacionadas = _conferencias_relacionadas_ao_usuario(
            nf,
            usuario,
            conferencias=_conferencias_nf_relacionadas(nf),
            produto_ids_usuario=produto_ids_usuario,
        )
        for conferencia in conferencias_relacionadas:
            if (
                conferencia.status == Conferencia.Status.EM_CONFERENCIA
                and conferencia.conferente_id
                and not usuario_esta_logado(conferencia.conferente)
            ):
                conferencia.status = Conferencia.Status.AGUARDANDO
                conferencia.save(update_fields=['status', 'updated_at'])
        conferencia_ativa_usuario = _conferencia_ativa_do_usuario(conferencias_relacionadas, usuario)
        conferencia_ativa_outro = _conferencia_ativa_outro_usuario(conferencias_relacionadas, usuario)
        conferencia_em_fluxo_obj = conferencia_ativa_usuario or conferencia_ativa_outro or _conferencia_mais_recente(
            [conferencia for conferencia in conferencias_relacionadas if conferencia.status in STATUS_CONFERENCIA_EM_FLUXO]
        )
        conferencia_em_fluxo = bool(
            conferencia_em_fluxo_obj is not None and conferencia_em_fluxo_obj.status in STATUS_CONFERENCIA_EM_FLUXO
        )

        status_separacao = validacao_fluxo['status_separacao']
        possui_liberacao_restricao = (
            nf.status == NotaFiscal.Status.LIBERADA_COM_RESTRICAO
            or any(conferencia.status == Conferencia.Status.LIBERADO_COM_RESTRICAO for conferencia in conferencias_relacionadas)
        )
        conferencia_liberada_lista = bool(
            validacao_fluxo['liberado'] or possui_liberacao_restricao or conferencia_em_fluxo
        )
        produto_ids_finalizados = _produto_ids_reservados(conferencias_relacionadas, STATUS_CONFERENCIA_FINALIZADA)
        itens_pendentes_conferencia = _itens_pendentes_conferencia(
            produto_ids_usuario,
            conferencia_em_fluxo_obj=conferencia_ativa_usuario or conferencia_ativa_outro,
            produto_ids_finalizados=produto_ids_finalizados,
        )
        status_elegivel = nf.status in {
            NotaFiscal.Status.PENDENTE,
            NotaFiscal.Status.NORMAL,
            NotaFiscal.Status.EM_CONFERENCIA,
            NotaFiscal.Status.LIBERADA_COM_RESTRICAO,
            NotaFiscal.Status.BLOQUEADA_COM_RESTRICAO,
        }
        fluxo_direto_balcao = bool(nf.balcao)
        if not conferencia_liberada_lista:
            logger.info(
                'CONFERENCIA_LISTAGEM_REMOVIDA nf_id=%s motivo=separacao_nao_liberada status_nf=%s conferencia_em_fluxo=%s',
                nf.id,
                nf.status,
                conferencia_em_fluxo,
            )
            continue
        if itens_pendentes_conferencia <= 0 and conferencia_em_fluxo_obj is None:
            logger.info(
                'CONFERENCIA_LISTAGEM_REMOVIDA nf_id=%s motivo=sem_itens_pendentes status_nf=%s',
                nf.id,
                nf.status,
            )
            continue
        if not (
            status_elegivel
            and (
                conferencia_liberada_lista
                or possui_liberacao_restricao
                or conferencia_em_fluxo_obj is not None
                or fluxo_direto_balcao
            )
        ):
            continue

        ultima_conferencia = _conferencia_mais_recente(conferencias_relacionadas)
        progresso = _progresso_conferencia(ultima_conferencia)
        bloqueado = bool(
            usuario is not None
            and conferencia_ativa_outro is not None
        )
        disponiveis.append(
            {
                'id': nf.id,
                'numero': nf.numero,
                'cliente': nf.cliente.nome,
                'rota': f'Balcao - {nf.rota.nome}' if nf.balcao else nf.rota.nome,
                'status_fiscal': nf.status_fiscal,
                'status': NotaFiscal.Status.EM_CONFERENCIA if conferencia_em_fluxo else nf.status,
                'status_separacao': status_separacao,
                'conferencia_liberada': conferencia_liberada_lista,
                'conferencia_bloqueio_motivo': validacao_fluxo['motivo'],
                'balcao': nf.balcao,
                'updated_ts': nf.updated_at.timestamp(),
                'data_referencia': timezone.localtime(nf.created_at).date().isoformat() if nf.created_at else (
                    nf.data_emissao.date().isoformat() if nf.data_emissao else timezone.localdate().isoformat()
                ),
                'progresso': progresso,
                'itens_pendentes_conferencia': itens_pendentes_conferencia,
                'bloqueado': bloqueado,
                'usuario_em_uso': (
                    (conferencia_em_fluxo_obj.conferente.nome or conferencia_em_fluxo_obj.conferente.username)
                    if conferencia_em_fluxo_obj is not None and conferencia_em_fluxo_obj.conferente_id
                    else ''
                ),
                'em_uso_por_mim': bool(
                    conferencia_ativa_usuario is not None
                ),
            }
        )
        if max_resultados and len(disponiveis) >= max_resultados:
            break
    disponiveis.sort(key=lambda nf: (0 if nf['balcao'] else 1, -nf['updated_ts']))
    logger.info(
        'FILTRO_DEBUG user_id=%s setores_usuario=%s filtros_aplicados=%s queryset_final_count=%s',
        getattr(usuario, 'id', None),
        sorted(_setores_usuario(usuario)) if usuario is not None and _setores_usuario(usuario) else [],
        'conferencia.nf_por_setor_usuario' if usuario is not None and not _usuario_pode_ver_todos_setores(usuario) else 'sem_restricao',
        len(disponiveis),
    )
    if max_resultados:
        disponiveis = disponiveis[:max_resultados]
    if usar_cache:
        cache.set(_cache_key_nfs_disponiveis(usuario), disponiveis, CONFERENCIA_LIST_CACHE_TTL)
    total_ms = round((time.perf_counter() - inicio_listagem) * 1000, 2)
    _log_conferencia_listagem(total_ms, usuario=usuario, total=len(disponiveis), cache_hit=cache_hit)
    return disponiveis


def _log_conferencia_listagem(total_ms, *, usuario, total, cache_hit):
    mensagem = (
        'CONFERENCIA_LISTAGEM_MS user_id=%s total_ms=%s total=%s cache_hit=%s'
        % (getattr(usuario, 'id', None), total_ms, total, cache_hit)
    )
    if total_ms > CONFERENCIA_LISTAGEM_WARNING_MS:
        logger.warning(mensagem)
    else:
        logger.info(mensagem)


def _filtrar_nfs_por_periodo_lista(nfs, data_inicio, data_fim):
    if data_inicio is None and data_fim is None:
        return nfs
    filtradas = []
    for nf in nfs:
        referencia = nf.get('data_referencia')
        if not referencia:
            filtradas.append(nf)
            continue
        try:
            data_ref = date.fromisoformat(str(referencia))
        except ValueError:
            filtradas.append(nf)
            continue
        if data_inicio is not None and data_ref < data_inicio:
            continue
        if data_fim is not None and data_ref > data_fim:
            continue
        filtradas.append(nf)
    return filtradas


def obter_proxima_nf_conferencia(usuario, *, excluir_nf_id=None):
    from apps.core.operacional_periodo import periodo_operacional_padrao

    data_inicio, data_fim = periodo_operacional_padrao()
    nfs = listar_nfs_disponiveis(
        usuario,
        somente_leitura=True,
        usar_cache=False,
        data_inicio=data_inicio,
        data_fim=data_fim,
        max_resultados=CONFERENCIA_LISTAGEM_MAX_RESULTADOS,
    )
    for nf in nfs:
        if excluir_nf_id and nf['id'] == excluir_nf_id:
            continue
        if nf.get('bloqueado'):
            continue
        return {'id': nf['id']}
    return None


def _itens_pendentes_conferencia(produto_ids_usuario, conferencia_em_fluxo_obj=None, produto_ids_finalizados=None):
    if produto_ids_finalizados is None:
        produto_ids_finalizados = set()
    if conferencia_em_fluxo_obj is not None:
        return conferencia_em_fluxo_obj.itens.filter(
            status__in=[ConferenciaItem.Status.AGUARDANDO, ConferenciaItem.Status.DIVERGENCIA]
        ).count()
    return max(len(set(produto_ids_usuario) - set(produto_ids_finalizados)), 0)


def iniciar_conferencia(nf_id, usuario):
    nf = (
        NotaFiscal.objects.select_related('cliente', 'rota')
        .defer('bairro')
        .prefetch_related('itens__produto', 'tarefas__itens', 'conferencias__itens')
        .get(id=nf_id)
    )

    if nf.status_fiscal == NotaFiscal.StatusFiscal.CANCELADA or not nf.ativa:
        _registrar_bloqueio_nf_cancelada(usuario, 'INICIO CONFERENCIA BLOQUEADO', nf)
        raise ConferenciaError(NF_CANCELADA_ERRO)
    _validar_setor_nf(nf, usuario)

    validacao_fluxo = avaliar_liberacao_conferencia(nf)
    if not validacao_fluxo['liberado']:
        raise ConferenciaError(validacao_fluxo['motivo'])

    itens_usuario = _itens_nf_por_usuario(nf, usuario)
    if not itens_usuario:
        raise ConferenciaError(TAREFA_SETOR_ERRO)
    setores_itens_usuario = {_setor_item_nf(item_nf) for item_nf in itens_usuario if _setor_item_nf(item_nf)}
    if setores_itens_usuario == {Setor.Codigo.FILTROS}:
        logger.info(
            'CRIANDO CONFERENCIA FILTROS nf_id=%s user_id=%s itens=%s',
            nf.id,
            getattr(usuario, 'id', None),
            len(itens_usuario),
        )

    conferencias_relacionadas = _conferencias_relacionadas_ao_usuario(
        nf,
        usuario,
        conferencias=list(nf.conferencias.select_related('conferente').all()),
    )
    conferencia_ativa = _conferencia_ativa_do_usuario(conferencias_relacionadas, usuario)
    if conferencia_ativa is not None:
        return _dados_conferencia(conferencia_ativa)

    produto_ids_reservados = _produto_ids_reservados(conferencias_relacionadas, STATUS_CONFERENCIA_RESERVA_ITENS)
    itens_disponiveis = [item_nf for item_nf in itens_usuario if item_nf.produto_id not in produto_ids_reservados]

    if not itens_disponiveis:
        if _conferencia_ativa_outro_usuario(conferencias_relacionadas, usuario) is not None:
            raise ConferenciaError('Itens dos setores do usuario ja estao em conferencia por outro usuario.')
        raise ConferenciaError('Nao ha itens pendentes de conferencia para os setores do usuario.')

    with transaction.atomic():
        conferencia = Conferencia.objects.create(nf=nf, conferente=usuario, status=Conferencia.Status.EM_CONFERENCIA)

        for item_nf in itens_disponiveis:
            ConferenciaItem.objects.create(
                conferencia=conferencia,
                produto=item_nf.produto,
                qtd_esperada=item_nf.quantidade,
                qtd_conferida=Decimal('0'),
                status=ConferenciaItem.Status.AGUARDANDO,
            )

        Log.objects.create(usuario=usuario, acao='INICIO CONFERENCIA', detalhe=f'Conferencia iniciada para NF {nf.numero}.')
        UserActivityLog.objects.create(
            usuario=usuario,
            tipo=UserActivityLog.Tipo.TAREFA_INICIO,
            tarefa=nf.tarefas.first(),
            timestamp=timezone.now(),
        )
    _invalidate_conferencia_operacional_cache(
        motivo='inicio_conferencia',
        nf_id=nf.id,
        setor=','.join(sorted(setores_itens_usuario)),
    )
    from apps.core.operacional_sessao_cache import preload_mapa_bipagem_conferencia

    preload_mapa_bipagem_conferencia(conferencia.id)
    return _dados_conferencia(conferencia)


def _resposta_bipagem_duplicada_conferencia(conferencia_id, codigo):
    from apps.core.operacional_sessao_cache import resolver_item_id_conferencia

    item_id, _ = resolver_item_id_conferencia(conferencia_id, codigo)
    if not item_id:
        return None
    item = (
        ConferenciaItem.objects.filter(pk=item_id)
        .only('qtd_esperada', 'qtd_conferida', 'produto__cod_prod')
        .select_related('produto')
        .first()
    )
    if item is None:
        return None
    return {
        'status': 'ok',
        'esperado': float(item.qtd_esperada),
        'conferido': float(item.qtd_conferida),
        'finalizado': False,
        'produto_cod': getattr(item.produto, 'cod_prod', '') or '',
    }


def bipar_conferencia(conferencia_id, codigo, usuario):
    from apps.core.bipagem_leitura import eh_bipagem_duplicada, sanitizar_entrada_scanner
    from apps.core.operacional_bipagem_metrics import BipagemMetrics
    from apps.core.operacional_sessao_cache import (
        invalidar_mapa_conferencia,
        resolver_item_id_conferencia,
    )
    from apps.core.operacional_side_effects import agendar_atualizar_status_nf, agendar_logs_bipagem_conferencia

    codigo = sanitizar_entrada_scanner(codigo)
    metricas = BipagemMetrics('conferencia', conferencia_id, getattr(usuario, 'id', None))
    inicio_bipagem = time.perf_counter()
    try:
        if eh_bipagem_duplicada(modulo='conferencia', entidade_id=conferencia_id, usuario_id=usuario.id, codigo=codigo):
            metricas.duplicada = True
            with metricas.fase('response'):
                resposta_dup = _resposta_bipagem_duplicada_conferencia(conferencia_id, codigo)
                if resposta_dup:
                    return resposta_dup

        with metricas.fase('cache'):
            item_id_cache, cache_hit = resolver_item_id_conferencia(conferencia_id, codigo)
            metricas.cache_hit = cache_hit

        nf_id = None
        nf_numero = ''
        produto_cod = ''
        finalizado = False
        status_final = None
        conferido = Decimal('0')
        esperado = Decimal('0')
        setor_cache = ''
        redirect_url_final = None
        finalizacao_inicio = None
        pendente_pos_commit = None

        def _executar():
            nonlocal nf_id, nf_numero, produto_cod, finalizado, conferido, esperado, setor_cache, finalizacao_inicio, pendente_pos_commit
            conferencia_local = None
            item_local = None
            with transaction.atomic():
                with metricas.fase('lock'):
                    lock_kwargs = {}
                    if connection.vendor == 'postgresql':
                        lock_kwargs = {'nowait': True, 'of': ('self',)}
                    else:
                        lock_kwargs = {'nowait': True}
                    conferencia_local = (
                        Conferencia.objects.select_for_update(**lock_kwargs)
                        .select_related('nf')
                        .only(
                            'id',
                            'status',
                            'conferente_id',
                            'nf_id',
                            'nf__id',
                            'nf__numero',
                            'nf__status',
                            'nf__status_fiscal',
                        )
                        .get(id=conferencia_id)
                    )
                    if conferencia_local.nf.status_fiscal == NotaFiscal.StatusFiscal.CANCELADA:
                        raise ConferenciaError(NF_CANCELADA_ERRO)
                    if conferencia_local.status not in {
                        Conferencia.Status.EM_CONFERENCIA,
                        Conferencia.Status.LIBERADO_COM_RESTRICAO,
                    }:
                        raise ConferenciaError('Conferencia nao esta em andamento.')
                    if conferencia_local.conferente_id != usuario.id:
                        raise ConferenciaError('Conferencia vinculada a outro usuario.')

                with metricas.fase('query'):
                    item_lock_kwargs = {'skip_locked': True}
                    if connection.vendor == 'postgresql':
                        item_lock_kwargs['of'] = ('self',)
                    itens_pendentes_qs = (
                        ConferenciaItem.objects.select_for_update(**item_lock_kwargs)
                        .filter(
                            conferencia_id=conferencia_id,
                            status=ConferenciaItem.Status.AGUARDANDO,
                            qtd_conferida__lt=F('qtd_esperada'),
                        )
                        .select_related('produto')
                        .only(
                            'id',
                            'conferencia_id',
                            'produto_id',
                            'qtd_esperada',
                            'qtd_conferida',
                            'status',
                            'produto__id',
                            'produto__cod_prod',
                            'produto__cod_ean',
                            'produto__codigo',
                            'produto__setor',
                        )
                        .order_by('id')
                    )
                    if item_id_cache:
                        item_esperado = itens_pendentes_qs.filter(pk=item_id_cache).first()
                    else:
                        item_esperado = None
                    if item_esperado is None:
                        itens_candidatos_qs, _ = filtrar_queryset_por_codigo_produto(itens_pendentes_qs, codigo)
                        item_esperado = itens_candidatos_qs.first()
                    if item_esperado is None:
                        item_esperado = itens_pendentes_qs.first()
                    if item_esperado is None:
                        raise ConferenciaError('Não existem itens pendentes para bipagem.')
                    try:
                        validacao = validar_produto(
                            codigo_lido=codigo,
                            item_id=item_esperado.id,
                            usuario=usuario,
                            item_model=ConferenciaItem,
                            tipo_validacao='CONFERENCIA',
                            item_travado=item_esperado,
                        )
                    except ProdutoValidacaoError as exc:
                        raise ConferenciaError(str(exc)) from exc

                    item_local = validacao.item
                    if item_local.status == ConferenciaItem.Status.DIVERGENCIA:
                        raise ConferenciaError('Item em divergencia nao pode ser bipado sem tratativa.')
                    if item_local.qtd_conferida >= item_local.qtd_esperada:
                        raise ConferenciaError('Quantidade conferida excede o esperado.')

                    nova_conferida = item_local.qtd_conferida + Decimal('1')
                    completo = nova_conferida >= item_local.qtd_esperada
                    agora = timezone.now()
                    novo_status = ConferenciaItem.Status.OK if completo else ConferenciaItem.Status.AGUARDANDO

                with metricas.fase('save'):
                    valores_update = {
                        'qtd_conferida': nova_conferida,
                        'status': novo_status,
                        'bipado_por_id': usuario.id,
                        'data_bipagem': agora,
                        'updated_at': agora,
                    }
                    if completo:
                        valores_update['motivo_divergencia'] = None
                        valores_update['observacao_divergencia'] = ''
                    ConferenciaItem.objects.filter(pk=item_local.pk).update(**valores_update)
                    item_local.qtd_conferida = nova_conferida
                    item_local.status = novo_status

                finalizado = False
                if completo:
                    finalizado = not ConferenciaItem.objects.filter(
                        conferencia_id=conferencia_id,
                        status=ConferenciaItem.Status.AGUARDANDO,
                        qtd_conferida__lt=F('qtd_esperada'),
                    ).exclude(pk=item_local.pk).exists()
                    if finalizado:
                        finalizacao_inicio = time.perf_counter()

                nf_id = conferencia_local.nf_id
                nf_numero = conferencia_local.nf.numero or ''
                produto_cod = item_local.produto.cod_prod
                conferido = nova_conferida
                esperado = item_local.qtd_esperada
                setor_cache = _setor_operacional_produto(item_local.produto)

            pendente_pos_commit = {
                'finalizado': finalizado,
                'nf_id': nf_id,
                'nf_numero': nf_numero,
                'produto_cod': produto_cod,
                'conferencia_status': conferencia_local.status,
                'nf_status': conferencia_local.nf.status,
                'setor_cache': setor_cache,
            }

        try:
            _executar()
        except OperationalError as exc:
            if connection.vendor == 'postgresql' and 'could not obtain lock' in str(exc).lower():
                raise ConferenciaError('Conferência em uso por outra operação. Tente novamente.') from exc
            raise

        if pendente_pos_commit:

            def _pos_commit_side_effects():
                payload = pendente_pos_commit
                try:
                    agendar_logs_bipagem_conferencia(
                        usuario_id=usuario.id,
                        nf_numero=payload['nf_numero'],
                        produto_cod=payload['produto_cod'],
                        tarefa_id=None,
                    )
                    if payload['finalizado']:
                        conferencia_liberada = (
                            payload['conferencia_status'] == Conferencia.Status.LIBERADO_COM_RESTRICAO
                            or payload['nf_status'] == NotaFiscal.Status.LIBERADA_COM_RESTRICAO
                        )
                        possui_divergencia = ConferenciaItem.objects.filter(
                            conferencia_id=conferencia_id,
                            status=ConferenciaItem.Status.DIVERGENCIA,
                        ).exists()
                        status_local = (
                            Conferencia.Status.CONCLUIDO_COM_RESTRICAO
                            if conferencia_liberada
                            else (Conferencia.Status.DIVERGENCIA if possui_divergencia else Conferencia.Status.OK)
                        )
                        agora_finalizacao = timezone.now()
                        Conferencia.objects.filter(pk=conferencia_id).update(
                            status=status_local,
                            updated_at=agora_finalizacao,
                        )
                        if conferencia_liberada:
                            detalhe = f'Conferencia da NF {payload["nf_numero"]} finalizada com restricao liberada.'
                        elif possui_divergencia:
                            detalhe = f'Conferencia da NF {payload["nf_numero"]} finalizada com divergencia.'
                        else:
                            detalhe = f'Conferencia da NF {payload["nf_numero"]} finalizada com sucesso.'
                        _agendar_finalizacao_conferencia_segura(
                            conferencia_id=conferencia_id,
                            nf_id=payload['nf_id'],
                            usuario_id=usuario.id,
                            possui_divergencia=possui_divergencia,
                            conferencia_liberada=conferencia_liberada,
                            detalhe_log=detalhe,
                            setor_cache=payload['setor_cache'],
                        )
                        invalidar_mapa_conferencia(conferencia_id)
                    else:
                        agendar_atualizar_status_nf(payload['nf_id'])
                except Exception as exc:
                    logger.warning(
                        'ASYNC_SIDE_EFFECT falha modulo=conferencia conferencia_id=%s erro=%s',
                        conferencia_id,
                        exc,
                    )

            transaction.on_commit(_pos_commit_side_effects)

        with metricas.fase('response'):
            with metricas.fase('serialize'):
                resposta = {
                    'status': 'ok',
                    'esperado': float(esperado),
                    'conferido': float(conferido),
                    'finalizado': finalizado,
                    'produto_cod': produto_cod,
                }
            if finalizado:
                with metricas.fase('redirect'):
                    from apps.core.operacional_transicao import anexar_transicao_conferencia

                    payload_final = anexar_transicao_conferencia({}, usuario, nf_id_atual=nf_id)
                    redirect_url_final = payload_final['redirect_url']
                    resposta.update(
                        {
                            'ok': True,
                            'finalizado': True,
                            'finalizada': True,
                            'redirect_url': payload_final['redirect_url'],
                            'proxima_nf_id': payload_final['proxima_nf_id'],
                            'tem_proxima': payload_final['tem_proxima'],
                        }
                    )
            return resposta
    finally:
        total_ms = round((time.perf_counter() - inicio_bipagem) * 1000, 2)
        if finalizado:
            total_finalizacao_ms = round(
                ((time.perf_counter() - finalizacao_inicio) * 1000) if finalizacao_inicio is not None else total_ms,
                2,
            )
            logger.info(
                'CONFERENCIA_FINALIZACAO_MS conferencia_id=%s user_id=%s total_ms=%s lock_ms=%.2f query_ms=%.2f response_ms=%.2f redirect_ms=%.2f redirect_url=%s',
                conferencia_id,
                getattr(usuario, 'id', None),
                total_finalizacao_ms,
                metricas._fases.get('lock', 0.0),
                metricas._fases.get('query', 0.0),
                metricas._fases.get('response', 0.0),
                metricas._fases.get('redirect', 0.0),
                redirect_url_final or '',
            )
        mensagem = (
            'CONFERENCIA_BIPAGEM_MS conferencia_id=%s user_id=%s total_ms=%s'
            % (conferencia_id, getattr(usuario, 'id', None), total_ms)
        )
        if total_ms > CONFERENCIA_BIPAGEM_WARNING_MS:
            logger.warning(mensagem)
        else:
            logger.info(mensagem)
        metricas.registrar()


@transaction.atomic
def registrar_divergencia(item_id, motivo, observacao, usuario):
    item = ConferenciaItem.objects.select_related('conferencia__nf', 'conferencia__conferente', 'produto').get(id=item_id)
    conferencia = item.conferencia

    if conferencia.status != Conferencia.Status.EM_CONFERENCIA:
        raise ConferenciaError('Conferencia nao esta ativa.')
    if conferencia.conferente_id != usuario.id:
        raise ConferenciaError('Somente o conferente vinculado pode registrar divergencia.')
    if item.qtd_conferida == item.qtd_esperada and item.qtd_esperada > 0:
        raise ConferenciaError('Item ja esta conferido e nao possui divergencia.')
    if not motivo:
        raise ConferenciaError('Motivo da divergencia e obrigatorio.')

    item.status = ConferenciaItem.Status.DIVERGENCIA
    item.motivo_divergencia = motivo
    item.observacao_divergencia = (observacao or '').strip()
    item.save(update_fields=['status', 'motivo_divergencia', 'observacao_divergencia', 'updated_at'])

    Log.objects.create(
        usuario=usuario,
        acao='DIVERGENCIA CONFERENCIA',
        detalhe=f'NF {conferencia.nf.numero} - produto {item.produto.cod_prod} com motivo {motivo}.',
    )
    _invalidate_conferencia_operacional_cache(
        motivo='divergencia_conferencia',
        nf_id=conferencia.nf_id,
        setor=_setor_operacional_produto(item.produto),
    )
    return _dados_item(item)


def _agendar_finalizacao_conferencia_fallback(
    *,
    conferencia_id,
    nf_id,
    usuario_id,
    possui_divergencia,
    conferencia_liberada,
    detalhe_log,
    setor_cache='',
):
    def _executar():
        logger.info(
            'CONFERENCIA_FINALIZACAO_SIDE_EFFECT_START conferencia_id=%s nf_id=%s fallback=%s',
            conferencia_id,
            nf_id,
            True,
        )
        try:
            from django.utils import timezone

            from apps.core.services.visibilidade_operacional_service import invalidate_monitoramento_conferencia_cache
            from apps.core.views_dashboard import invalidate_dashboard_separacao_cache

            conferencia = (
                Conferencia.objects.select_related('nf', 'nf__rota')
                .prefetch_related('itens__produto')
                .get(id=conferencia_id)
            )
            nf = conferencia.nf
            if nf is not None:
                sincronizar_status_operacional_nf(nf)

            if possui_divergencia and not conferencia_liberada:
                _gerar_retorno_para_separacao(conferencia)

            Log.objects.create(usuario_id=usuario_id, acao='FINALIZACAO CONFERENCIA', detalhe=detalhe_log)
            tarefa_id = Tarefa.objects.filter(nf_id=nf_id).values_list('id', flat=True).first()
            UserActivityLog.objects.create(
                usuario_id=usuario_id,
                tipo=UserActivityLog.Tipo.TAREFA_FIM,
                tarefa_id=tarefa_id,
                timestamp=timezone.now(),
            )
            invalidate_nfs_disponiveis_cache(motivo='finalizacao_conferencia', nf_id=nf_id, setor=setor_cache)
            invalidate_dashboard_separacao_cache(motivo='finalizacao_conferencia')
            invalidate_monitoramento_conferencia_cache(
                motivo='finalizacao_conferencia',
                nf_id=nf_id,
                setor=setor_cache,
            )
            logger.info(
                'CONFERENCIA_FINALIZACAO_SIDE_EFFECT_DONE conferencia_id=%s nf_id=%s fallback=%s',
                conferencia_id,
                nf_id,
                True,
            )
        except Exception:
            logger.exception(
                'CONFERENCIA_FINALIZACAO_SIDE_EFFECT_ERROR conferencia_id=%s nf_id=%s fallback=%s',
                conferencia_id,
                nf_id,
                True,
            )

    transaction.on_commit(_executar)


def _agendar_finalizacao_conferencia_segura(**kwargs):
    conferencia_id = kwargs.get('conferencia_id')
    nf_id = kwargs.get('nf_id')
    logger.info(
        'CONFERENCIA_FINALIZACAO_SIDE_EFFECT_SCHEDULE conferencia_id=%s nf_id=%s',
        conferencia_id,
        nf_id,
    )
    try:
        modulo = import_module('apps.core.operacional_side_effects')
        agendador = getattr(modulo, 'agendar_finalizacao_conferencia')
    except Exception:
        logger.exception(
            'CONFERENCIA_FINALIZACAO_SIDE_EFFECT_IMPORT_ERROR conferencia_id=%s nf_id=%s',
            conferencia_id,
            nf_id,
        )
        _agendar_finalizacao_conferencia_fallback(**kwargs)
        return

    try:
        agendador(**kwargs)
    except Exception:
        logger.exception(
            'CONFERENCIA_FINALIZACAO_SIDE_EFFECT_SCHEDULE_ERROR conferencia_id=%s nf_id=%s',
            conferencia_id,
            nf_id,
        )
        _agendar_finalizacao_conferencia_fallback(**kwargs)


def finalizar_conferencia(conferencia_id, usuario, *, resposta_minima=False):
    from apps.core.operacional_transicao import anexar_transicao_conferencia

    inicio = time.perf_counter()
    query_inicio = time.perf_counter()

    conferencia = (
        Conferencia.objects.select_related('nf')
        .only(
            'id',
            'status',
            'conferente_id',
            'nf_id',
            'nf__id',
            'nf__numero',
            'nf__status',
            'nf__status_fiscal',
        )
        .get(id=conferencia_id)
    )
    status_anterior = conferencia.status
    logger.info(
        'CONFERENCIA_FINALIZACAO_INICIO conferencia_id=%s nf_id=%s status_atual=%s user_id=%s',
        conferencia_id,
        conferencia.nf_id,
        status_anterior,
        getattr(usuario, 'id', None),
    )
    if conferencia.nf.status_fiscal == NotaFiscal.StatusFiscal.CANCELADA:
        _registrar_bloqueio_nf_cancelada(usuario, 'CONFERENCIA BLOQUEADA', conferencia.nf)
        raise ConferenciaError(NF_CANCELADA_ERRO)
    _validar_setor_nf(conferencia.nf, usuario)
    if conferencia.status not in {Conferencia.Status.EM_CONFERENCIA, Conferencia.Status.LIBERADO_COM_RESTRICAO}:
        raise ConferenciaError('Conferencia nao esta em andamento.')
    if conferencia.conferente_id != usuario.id:
        raise ConferenciaError('Conferencia vinculada a outro usuario.')

    if not ConferenciaItem.objects.filter(conferencia_id=conferencia_id).exists():
        raise ConferenciaError('Conferencia sem itens para finalizar.')

    conferencia_liberada = (
        conferencia.status == Conferencia.Status.LIBERADO_COM_RESTRICAO
        or conferencia.nf.status == NotaFiscal.Status.LIBERADA_COM_RESTRICAO
    )
    if (
        not conferencia_liberada
        and ConferenciaItem.objects.filter(
            conferencia_id=conferencia_id,
            status=ConferenciaItem.Status.AGUARDANDO,
        ).exists()
    ):
        raise ConferenciaError('Existem itens pendentes de bipagem ou tratativa.')

    possui_divergencia = ConferenciaItem.objects.filter(
        conferencia_id=conferencia_id,
        status=ConferenciaItem.Status.DIVERGENCIA,
    ).exists()
    query_ms = round((time.perf_counter() - query_inicio) * 1000, 2)

    if conferencia_liberada:
        novo_status = Conferencia.Status.CONCLUIDO_COM_RESTRICAO
    else:
        novo_status = Conferencia.Status.DIVERGENCIA if possui_divergencia else Conferencia.Status.OK

    save_inicio = time.perf_counter()
    agora = timezone.now()
    with transaction.atomic():
        Conferencia.objects.filter(pk=conferencia_id).update(status=novo_status, updated_at=agora)
        nf_id = conferencia.nf_id
        nf_numero = conferencia.nf.numero or ''
        if conferencia_liberada:
            detalhe = f'Conferencia da NF {nf_numero} finalizada com restricao liberada.'
        elif possui_divergencia:
            detalhe = f'Conferencia da NF {nf_numero} finalizada com divergencia.'
        else:
            detalhe = f'Conferencia da NF {nf_numero} finalizada com sucesso.'

        logger.info(
            'CONFERENCIA_FINALIZACAO_STATUS conferencia_id=%s nf_id=%s status_antes=%s status_depois=%s possui_divergencia=%s conferencia_liberada=%s',
            conferencia_id,
            nf_id,
            status_anterior,
            novo_status,
            possui_divergencia,
            conferencia_liberada,
        )
        _agendar_finalizacao_conferencia_segura(
            conferencia_id=conferencia_id,
            nf_id=nf_id,
            usuario_id=usuario.id,
            possui_divergencia=possui_divergencia,
            conferencia_liberada=conferencia_liberada,
            detalhe_log=detalhe,
        )
    save_ms = round((time.perf_counter() - save_inicio) * 1000, 2)
    total_ms = round((time.perf_counter() - inicio) * 1000, 2)
    mensagem_metrica = (
        f'CONFERENCIA_FINALIZACAO_MS conferencia_id={conferencia_id} total_ms={total_ms} '
        f'query_ms={query_ms} save_ms={save_ms}'
    )
    if total_ms > CONFERENCIA_FINALIZACAO_WARNING_MS:
        logger.warning(mensagem_metrica)
    else:
        logger.info(mensagem_metrica)

    payload_transicao = anexar_transicao_conferencia({}, usuario, nf_id_atual=nf_id)
    if resposta_minima:
        retorno_minimo = {
            'ok': True,
            'finalizado': True,
            'finalizada': True,
            'redirect_url': payload_transicao['redirect_url'],
            'proxima_nf_id': payload_transicao['proxima_nf_id'],
            'tem_proxima': payload_transicao['tem_proxima'],
        }
        return retorno_minimo

    retorno = {
        'status': novo_status,
        'finalizado': True,
        'finalizada': True,
        'redirect_url': payload_transicao['redirect_url'],
        'id': conferencia_id,
        'nf_id': nf_id,
        'proxima_nf_id': payload_transicao['proxima_nf_id'],
        'tem_proxima': payload_transicao['tem_proxima'],
    }
    return retorno


def _obter_conferencia_em_andamento(conferencia_id, usuario):
    conferencia = (
        Conferencia.objects.select_related('nf', 'conferente', 'nf__rota')
        .prefetch_related('itens__produto')
        .get(id=conferencia_id)
    )
    if conferencia.nf.status_fiscal == NotaFiscal.StatusFiscal.CANCELADA:
        _registrar_bloqueio_nf_cancelada(usuario, 'CONFERENCIA BLOQUEADA', conferencia.nf)
        raise ConferenciaError(NF_CANCELADA_ERRO)
    _validar_setor_nf(conferencia.nf, usuario)
    if conferencia.status not in {Conferencia.Status.EM_CONFERENCIA, Conferencia.Status.LIBERADO_COM_RESTRICAO}:
        raise ConferenciaError('Conferencia nao esta em andamento.')
    if conferencia.conferente_id != usuario.id:
        raise ConferenciaError('Conferencia vinculada a outro usuario.')
    return conferencia


def _gerar_retorno_para_separacao(conferencia):
    itens_divergentes = list(conferencia.itens.filter(status=ConferenciaItem.Status.DIVERGENCIA).select_related('produto'))
    if not itens_divergentes:
        return

    agrupados_operacionais = {}
    for item in itens_divergentes:
        quantidade_retorno = item.qtd_esperada - item.qtd_conferida
        if quantidade_retorno <= 0:
            quantidade_retorno = item.qtd_esperada or Decimal('1')
        setor = _setor_operacional_produto(item.produto)
        tipo_embalagem = normalizar_tipo_embalagem(getattr(item.produto, 'embalagem', None))
        agrupados_operacionais.setdefault((setor, tipo_embalagem), []).append((item.produto, quantidade_retorno))

    for (setor, tipo_embalagem), itens in agrupados_operacionais.items():
        tarefa, _onda = obter_tarefa_separacao_com_fallback_onda(
            nf=conferencia.nf,
            rota=conferencia.nf.rota,
            setor=setor,
            tipo_embalagem=tipo_embalagem,
        )
        for produto, quantidade in itens:
            item_tarefa = TarefaItem.objects.filter(tarefa=tarefa, produto=produto, nf=conferencia.nf).first()
            if item_tarefa is None:
                TarefaItem.objects.create(tarefa=tarefa, nf=conferencia.nf, produto=produto, quantidade_total=quantidade)
                registrar_item_tarefa_onda(tarefa=tarefa, quantidade=quantidade)
                continue
            item_tarefa.quantidade_total += quantidade
            item_tarefa.save(update_fields=['quantidade_total', 'updated_at'])
            registrar_item_tarefa_onda(tarefa=tarefa, quantidade=quantidade)


def _tarefas_relacionadas_nf(nf):
    return list(
        Tarefa.objects.filter(Q(nf=nf) | Q(itens__nf=nf))
        .select_related('onda', 'rota')
        .distinct()
    )


def _dados_conferencia(conferencia):
    conferencia = Conferencia.objects.select_related('nf', 'conferente').prefetch_related('itens__produto').get(id=conferencia.id)
    return {
        'id': conferencia.id,
        'nf_id': conferencia.nf_id,
        'nf_numero': conferencia.nf.numero,
        'conferente': conferencia.conferente.nome,
        'status': conferencia.status,
        'setores': sorted({_setor_operacional_produto(item.produto) for item in conferencia.itens.all()}),
        'progresso': _progresso_conferencia(conferencia),
    }


def _dados_item(item):
    return {
        'item_id': item.id,
        'produto': item.produto.cod_prod,
        'ean': item.produto.cod_ean,
        'status': item.status,
        'esperado': float(item.qtd_esperada),
        'conferido': float(item.qtd_conferida),
        'percentual': float(_percentual(item.qtd_conferida, item.qtd_esperada)),
        'progresso': _progresso_conferencia(item.conferencia),
    }


def _dados_item_operacional(item):
    if item is None:
        return None
    return {
        'item_id': item.id,
        'produto': item.produto.cod_prod,
        'descricao': item.produto.descricao,
        'ean': item.produto.cod_ean,
        'status': item.status,
        'esperado': float(item.qtd_esperada),
        'conferido': float(item.qtd_conferida),
        'bipado_por': (
            item.bipado_por.nome or item.bipado_por.username
            if item.bipado_por is not None
            else ''
        ),
        'data_bipagem': item.data_bipagem.isoformat() if item.data_bipagem else None,
    }


def _progresso_conferencia(conferencia):
    if conferencia is None:
        return {'esperado': 0.0, 'conferido': 0.0, 'percentual': 0.0}
    itens = list(conferencia.itens.all())
    esperado = sum((item.qtd_esperada for item in itens), Decimal('0'))
    conferido = sum((item.qtd_conferida for item in itens), Decimal('0'))
    return {
        'esperado': float(esperado),
        'conferido': float(conferido),
        'percentual': float(_percentual(conferido, esperado)),
    }


def _percentual(conferido, esperado):
    if not esperado:
        return Decimal('0')
    return (conferido / esperado * Decimal('100')).quantize(Decimal('0.01'))


def _registrar_bloqueio_nf_cancelada(usuario, acao, nf):
    Log.objects.create(
        usuario=usuario,
        acao=acao,
        detalhe=f'NF {nf.numero} bloqueada. Motivo: NF CANCELADA.',
    )