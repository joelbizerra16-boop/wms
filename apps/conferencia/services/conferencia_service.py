from datetime import date
from decimal import Decimal
import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.db import OperationalError, connection, transaction
from django.db.models import F, Prefetch, Q
from django.utils import timezone

from apps.conferencia.models import Conferencia, ConferenciaItem
from apps.core.services.produto_validacao_service import (
    ProdutoValidacaoError,
    selecionar_item_por_codigo_lido,
    validar_produto,
)
from apps.logs.models import Log, UserActivityLog
from apps.nf.models import NotaFiscal, NotaFiscalItem
from apps.nf.services.consistencia_service import sanear_consistencia_nf, separacao_concluida_nf
from apps.nf.services.status_service import atualizar_status_nf, sincronizar_status_operacional_nf
from apps.produtos.models import Produto
from apps.tarefas.models import Tarefa, TarefaItem
from apps.usuarios.models import Setor
from apps.usuarios.session_utils import usuario_esta_logado

logger = logging.getLogger(__name__)


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
    itens_qs = _itens_separacao_nf_qs(nf)
    total_itens = itens_qs.count()
    if total_itens == 0:
        return {'status_separacao': 'PENDENTE', 'itens_pendentes': 0, 'itens_separados': 0, 'total_itens': 0}

    itens_pendentes = itens_qs.filter(quantidade_separada__lt=F('quantidade_total')).count()
    itens_separados = max(total_itens - itens_pendentes, 0)
    status_separacao = 'SEPARADO' if itens_pendentes == 0 else 'PENDENTE'
    return {
        'status_separacao': status_separacao,
        'itens_pendentes': itens_pendentes,
        'itens_separados': itens_separados,
        'total_itens': total_itens,
    }


def avaliar_liberacao_conferencia(nf):
    """
    Regra obrigatória de fluxo:
    - Conferência só libera quando separação estiver concluída e sem itens pendentes.
    - Exceção: quando não houver item de separação relacionado para a NF, considera
      'SEPARADO_COM_RESTRICAO' e permite seguir.
    """
    resumo = _resumo_separacao_nf(nf)
    if resumo['status_separacao'] != 'SEPARADO':
        return {
            'liberado': False,
            'motivo': 'Pedido ainda não foi separado',
            'status_fluxo': 'BLOQUEADO',
            'itens_pendentes': resumo['itens_pendentes'],
            'total_itens': resumo['total_itens'],
            'itens_separados': resumo['itens_separados'],
        }
    return {
        'liberado': True,
        'motivo': '',
        'status_fluxo': 'AGUARDANDO',
        'itens_pendentes': 0,
        'total_itens': resumo['total_itens'],
        'itens_separados': resumo['itens_separados'],
    }


def listar_nfs_disponiveis(
    usuario=None,
    *,
    somente_leitura=False,
    usar_cache=True,
    data_inicio=None,
    data_fim=None,
):
    if usuario is not None and not _usuario_pode_ver_todos_setores(usuario) and not _setores_usuario(usuario):
        return []
    if usar_cache:
        cache_key = _cache_key_nfs_disponiveis(usuario)
        cached = cache.get(cache_key)
        if cached is not None:
            if data_inicio is None and data_fim is None:
                return cached
            return _filtrar_nfs_por_periodo_lista(cached, data_inicio, data_fim)
    tarefa_itens_prefetch = Prefetch(
        'itens',
        queryset=TarefaItem.objects.select_related('tarefa').only(
            'id',
            'tarefa_id',
            'nf_id',
            'quantidade_total',
            'quantidade_separada',
            'possui_restricao',
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

    disponiveis = []
    for nf in nfs:
        consistencia = sanear_consistencia_nf(nf, actor=usuario)
        if not consistencia['valida']:
            continue
        if not somente_leitura:
            atualizar_status_nf(nf)

        validacao_fluxo = avaliar_liberacao_conferencia(nf)
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

        status_separacao = 'SEPARADO' if validacao_fluxo['liberado'] else 'PENDENTE'
        possui_liberacao_restricao = (
            nf.status == NotaFiscal.Status.LIBERADA_COM_RESTRICAO
            or any(conferencia.status == Conferencia.Status.LIBERADO_COM_RESTRICAO for conferencia in conferencias_relacionadas)
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
        # Conferencia so lista pedido totalmente separado.
        if status_separacao != 'SEPARADO':
            continue
        if itens_pendentes_conferencia <= 0 and conferencia_em_fluxo_obj is None:
            continue
        if not (
            status_elegivel
            and (
                separacao_concluida_nf(nf)
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
                'status': nf.status,
                'status_separacao': status_separacao,
                'conferencia_liberada': validacao_fluxo['liberado'],
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
    disponiveis.sort(key=lambda nf: (0 if nf['balcao'] else 1, -nf['updated_ts']))
    if usar_cache:
        cache.set(_cache_key_nfs_disponiveis(usuario), disponiveis, CONFERENCIA_LIST_CACHE_TTL)
    return disponiveis


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
    return _dados_conferencia(conferencia)


def bipar_conferencia(conferencia_id, codigo, usuario):
    from apps.core.operacional_bipagem_metrics import BipagemMetrics
    from apps.core.operacional_side_effects import agendar_atualizar_status_nf, agendar_logs_bipagem_conferencia

    metricas = BipagemMetrics('conferencia', conferencia_id, getattr(usuario, 'id', None))
    try:
        nf_id = None
        nf_numero = ''
        produto_cod = ''
        finalizado = False
        conferido = Decimal('0')
        esperado = Decimal('0')
        setor_cache = ''

        def _executar():
            nonlocal nf_id, nf_numero, produto_cod, finalizado, conferido, esperado, setor_cache
            with transaction.atomic():
                with metricas.fase('lock'):
                    lock_kwargs = {}
                    if connection.vendor == 'postgresql':
                        lock_kwargs = {'nowait': True, 'of': ('self',)}
                    else:
                        lock_kwargs = {'nowait': True}
                    conferencia_local = Conferencia.objects.select_for_update(**lock_kwargs).get(id=conferencia_id)
                    status_fiscal_nf = NotaFiscal.objects.filter(id=conferencia_local.nf_id).values_list(
                        'status_fiscal', flat=True
                    ).first()
                    if status_fiscal_nf == NotaFiscal.StatusFiscal.CANCELADA:
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
                    itens_pendentes = list(
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
                    if not itens_pendentes:
                        raise ConferenciaError('Não existem itens pendentes para bipagem.')

                    item_esperado = selecionar_item_por_codigo_lido(codigo, itens_pendentes, fallback=itens_pendentes[0])
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

                itens_restantes = []
                for item_pendente in itens_pendentes:
                    if item_pendente.id == item_local.id:
                        if not completo:
                            itens_restantes.append(item_local)
                        continue
                    itens_restantes.append(item_pendente)

                finalizado = not itens_restantes
                nf_id = conferencia_local.nf_id
                nf_numero = conferencia_local.nf.numero
                produto_cod = item_local.produto.cod_prod
                conferido = nova_conferida
                esperado = item_local.qtd_esperada
                setor_cache = _setor_operacional_produto(item_local.produto)

                agendar_logs_bipagem_conferencia(
                    usuario_id=usuario.id,
                    nf_numero=nf_numero,
                    produto_cod=produto_cod,
                    tarefa_id=None,
                )
                if not finalizado:
                    agendar_atualizar_status_nf(nf_id)

                    def _invalidar_apos_bipagem():
                        _invalidate_conferencia_operacional_cache(
                            motivo='bipagem_conferencia',
                            nf_id=nf_id,
                            setor=setor_cache,
                        )

                    transaction.on_commit(_invalidar_apos_bipagem)

        try:
            _executar()
        except OperationalError as exc:
            if connection.vendor == 'postgresql' and 'could not obtain lock' in str(exc).lower():
                raise ConferenciaError('Conferência em uso por outra operação. Tente novamente.') from exc
            raise

        with metricas.fase('response'):
            resposta = {
                'status': 'ok',
                'esperado': float(esperado),
                'conferido': float(conferido),
                'finalizado': finalizado,
            }
            if finalizado:
                finalizar_conferencia(conferencia_id, usuario)
                conferencia_final = Conferencia.objects.only('id', 'status').get(id=conferencia_id)
                resposta['conferencia'] = {
                    'id': conferencia_final.id,
                    'status': conferencia_final.status,
                    'progresso': {'percentual': 100.0},
                }
                from apps.core.operacional_transicao import anexar_transicao_conferencia

                anexar_transicao_conferencia(resposta, usuario, nf_id_atual=nf_id)
            return resposta
    finally:
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


def finalizar_conferencia(conferencia_id, usuario):
    conferencia = _obter_conferencia_em_andamento(conferencia_id, usuario)
    itens = list(conferencia.itens.select_related('produto').all())

    if not itens:
        raise ConferenciaError('Conferencia sem itens para finalizar.')
    conferencia_liberada = conferencia.status == Conferencia.Status.LIBERADO_COM_RESTRICAO or conferencia.nf.status == NotaFiscal.Status.LIBERADA_COM_RESTRICAO
    if any(item.status == ConferenciaItem.Status.AGUARDANDO for item in itens) and not conferencia_liberada:
        raise ConferenciaError('Existem itens pendentes de bipagem ou tratativa.')

    possui_divergencia = any(item.status == ConferenciaItem.Status.DIVERGENCIA for item in itens)
    with transaction.atomic():
        if conferencia_liberada:
            conferencia.status = Conferencia.Status.CONCLUIDO_COM_RESTRICAO
        else:
            conferencia.status = Conferencia.Status.DIVERGENCIA if possui_divergencia else Conferencia.Status.OK
        conferencia.save(update_fields=['status', 'updated_at'])

        nf = conferencia.nf
        sincronizar_status_operacional_nf(nf)

        if conferencia.status == Conferencia.Status.CONCLUIDO_COM_RESTRICAO:
            detalhe = f'Conferencia da NF {nf.numero} finalizada com restricao liberada.'
        elif possui_divergencia:
            _gerar_retorno_para_separacao(conferencia)
            detalhe = f'Conferencia da NF {nf.numero} finalizada com divergencia.'
        else:
            detalhe = f'Conferencia da NF {nf.numero} finalizada com sucesso.'

        Log.objects.create(usuario=usuario, acao='FINALIZACAO CONFERENCIA', detalhe=detalhe)
        UserActivityLog.objects.create(
            usuario=usuario,
            tipo=UserActivityLog.Tipo.TAREFA_FIM,
            tarefa=conferencia.nf.tarefas.first(),
            timestamp=timezone.now(),
        )
    nf_id = conferencia.nf_id
    setor = ','.join(sorted({_setor_operacional_produto(item.produto) for item in itens}))
    _invalidate_conferencia_operacional_cache(
        motivo='finalizacao_conferencia',
        nf_id=nf_id,
        setor=setor,
    )
    dados = _dados_conferencia(conferencia)
    from apps.core.operacional_transicao import anexar_transicao_conferencia

    return anexar_transicao_conferencia(dados, usuario, nf_id_atual=nf_id)


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

    filtros = []
    normais = []
    for item in itens_divergentes:
        quantidade_retorno = item.qtd_esperada - item.qtd_conferida
        if quantidade_retorno <= 0:
            quantidade_retorno = item.qtd_esperada or Decimal('1')
        if _setor_operacional_produto(item.produto) == Setor.Codigo.FILTROS:
            filtros.append((item.produto, quantidade_retorno))
        else:
            normais.append((item.produto, quantidade_retorno))

    if filtros:
        tarefa_filtro = Tarefa.objects.create(
            nf=conferencia.nf,
            tipo=Tarefa.Tipo.FILTRO,
            setor=Setor.Codigo.FILTROS,
            rota=conferencia.nf.rota,
            status=Tarefa.Status.ABERTO,
        )
        for produto, quantidade in filtros:
            TarefaItem.objects.create(tarefa=tarefa_filtro, nf=conferencia.nf, produto=produto, quantidade_total=quantidade)

    if normais:
        agrupados_por_setor = {}
        for produto, quantidade in normais:
            agrupados_por_setor.setdefault(_setor_operacional_produto(produto), []).append((produto, quantidade))

        for setor, itens in agrupados_por_setor.items():
            tarefa = Tarefa.objects.filter(
                nf__isnull=True,
                tipo=Tarefa.Tipo.ROTA,
                setor=setor,
                rota=conferencia.nf.rota,
                status=Tarefa.Status.ABERTO,
            ).first()
            if tarefa is None:
                tarefa = Tarefa.objects.create(
                    nf=None,
                    tipo=Tarefa.Tipo.ROTA,
                    setor=setor,
                    rota=conferencia.nf.rota,
                    status=Tarefa.Status.ABERTO,
                )
            for produto, quantidade in itens:
                item_tarefa = TarefaItem.objects.filter(tarefa=tarefa, produto=produto, nf=conferencia.nf).first()
                if item_tarefa is None:
                    TarefaItem.objects.create(tarefa=tarefa, nf=conferencia.nf, produto=produto, quantidade_total=quantidade)
                    continue
                item_tarefa.quantidade_total += quantidade
                item_tarefa.save(update_fields=['quantidade_total', 'updated_at'])


def _tarefas_relacionadas_nf(nf):
    setores_nf = {
        _setor_operacional_produto(item.produto)
        for item in _itens_nf_relacionados(nf)
        if item.produto_id is not None
        if _setor_operacional_produto(item.produto) != Setor.Codigo.FILTROS
    }
    tarefas = [tarefa for tarefa in _tarefas_nf_relacionadas(nf) if tarefa.tipo == Tarefa.Tipo.FILTRO]
    if not setores_nf:
        return tarefas

    tarefas_rota = list(
        Tarefa.objects.filter(
            nf__isnull=True,
            tipo=Tarefa.Tipo.ROTA,
            rota=nf.rota,
            setor__in=setores_nf,
        )
    )
    if len({tarefa.setor for tarefa in tarefas_rota}) != len(setores_nf):
        return tarefas
    return tarefas + tarefas_rota


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