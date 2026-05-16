import logging
import time
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, Max, Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.conferencia.models import Conferencia, ConferenciaItem
from apps.conferencia.services.conferencia_service import listar_nfs_disponiveis
from apps.nf.models import NotaFiscal
from apps.produtos.models import Produto
from apps.tarefas.models import Tarefa
from apps.tarefas.services.separacao_service import listar_tarefas_disponiveis
from apps.usuarios.models import Setor

logger = logging.getLogger(__name__)
CONFERENCIA_MONITORAMENTO_CACHE_TTL = 15
CACHE_VERSION_KEY_MONITORAMENTO_CONFERENCIA = 'dashboard:conferencia:version'
STATUS_CONFERENCIA_HISTORICO = {
    Conferencia.Status.OK,
    Conferencia.Status.CONCLUIDO_COM_RESTRICAO,
}


def _cache_version_monitoramento_conferencia():
    return int(cache.get(CACHE_VERSION_KEY_MONITORAMENTO_CONFERENCIA, 1) or 1)


def invalidate_monitoramento_conferencia_cache(*, motivo='', nf_id=None, setor=None):
    nova_versao = _cache_version_monitoramento_conferencia() + 1
    cache.set(CACHE_VERSION_KEY_MONITORAMENTO_CONFERENCIA, nova_versao, None)
    if motivo or nf_id or setor:
        logger.info(
            'INVALIDANDO_DASHBOARD_CONFERENCIA motivo=%s nf_id=%s setor=%s versao=%s',
            motivo or '',
            nf_id,
            setor or '',
            nova_versao,
        )


def _nome_cliente_nf(nf):
    fallback = 'CLIENTE NAO INFORMADO'
    if nf is None or not getattr(nf, 'cliente_id', None):
        return fallback
    try:
        cliente = nf.cliente
    except ObjectDoesNotExist:
        return fallback
    return getattr(cliente, 'nome', '') or fallback


def _contains_busca(haystack, busca):
    if not busca:
        return True
    return busca in (haystack or '').lower()


def _usuario_pode_ver_todos_setores(usuario):
    return bool(getattr(usuario, 'is_superuser', False))


def _setores_usuario(usuario):
    if usuario is None:
        return set()
    setores = list(usuario.setores.values_list('nome', flat=True))
    if not setores and getattr(usuario, 'setor', None) and usuario.setor != Setor.Codigo.NAO_ENCONTRADO:
        setores = [usuario.setor]
    return set(setores)


def _normalizar_setor_operacional(valor):
    setor = (valor or '').strip().upper()
    if setor == 'FILTRO':
        return Setor.Codigo.FILTROS
    if setor == 'NAO ENCONTRADO':
        return Setor.Codigo.NAO_ENCONTRADO
    return setor


def _setores_por_nf_ids_batch(nf_ids):
    if not nf_ids:
        return {}
    setores_por_nf = {nf_id: set() for nf_id in nf_ids}
    for nf_id, setor in Tarefa.objects.filter(nf_id__in=nf_ids).exclude(setor='').values_list('nf_id', 'setor'):
        normalizado = _normalizar_setor_operacional(setor)
        if normalizado:
            setores_por_nf.setdefault(nf_id, set()).add(normalizado)
    faltantes = [nf_id for nf_id, setores in setores_por_nf.items() if not setores]
    if not faltantes:
        return setores_por_nf
    for nf in NotaFiscal.objects.filter(id__in=faltantes).prefetch_related('itens__produto').only('id'):
        nf_id = nf.id
        for item in nf.itens.all():
            if item.produto_id is None:
                setores_por_nf[nf_id].add(Setor.Codigo.NAO_ENCONTRADO)
                continue
            setor_prod = _normalizar_setor_operacional(getattr(item.produto, 'setor', None))
            if setor_prod:
                setores_por_nf[nf_id].add(setor_prod)
            elif item.produto.categoria == Produto.Categoria.FILTROS:
                setores_por_nf[nf_id].add(Setor.Codigo.FILTROS)
            elif item.produto.categoria == Produto.Categoria.LUBRIFICANTE:
                setores_por_nf[nf_id].add(Setor.Codigo.LUBRIFICANTE)
            elif item.produto.categoria == Produto.Categoria.AGREGADO:
                setores_por_nf[nf_id].add(Setor.Codigo.AGREGADO)
            else:
                setores_por_nf[nf_id].add(Setor.Codigo.NAO_ENCONTRADO)
    return setores_por_nf


def _carregar_historico_conferencia_dashboard(
    usuario,
    *,
    data_inicio=None,
    data_fim=None,
    busca='',
    ids_operacionais=None,
):
    ids_operacionais = ids_operacionais or set()
    setores_usuario = _setores_usuario(usuario)
    pode_ver_todos = _usuario_pode_ver_todos_setores(usuario)
    if not pode_ver_todos and not setores_usuario:
        return []

    limite = int(getattr(settings, 'DASHBOARD_CONFERENCIA_HISTORICO_LIMIT', 100))
    base_qs = (
        Conferencia.objects.filter(status__in=STATUS_CONFERENCIA_HISTORICO)
        .filter(nf__ativa=True)
        .exclude(nf__status_fiscal=NotaFiscal.StatusFiscal.CANCELADA)
    )
    if data_inicio:
        base_qs = base_qs.filter(updated_at__date__gte=data_inicio)
    if data_fim:
        base_qs = base_qs.filter(updated_at__date__lte=data_fim)

    ultima_por_nf = {}
    for conf_id, nf_id in base_qs.order_by('-updated_at').values_list('id', 'nf_id'):
        if nf_id in ids_operacionais or nf_id in ultima_por_nf:
            continue
        ultima_por_nf[nf_id] = conf_id
        if len(ultima_por_nf) >= limite:
            break

    if not ultima_por_nf:
        return []

    conferencias = (
        Conferencia.objects.filter(id__in=ultima_por_nf.values())
        .select_related('nf', 'nf__cliente', 'nf__rota')
        .defer('nf__bairro')
        .annotate(
            total_esperado=Coalesce(Sum('itens__qtd_esperada'), Decimal('0')),
            total_conferido=Coalesce(Sum('itens__qtd_conferida'), Decimal('0')),
            pendentes=Count('itens', filter=~Q(itens__status=ConferenciaItem.Status.OK)),
        )
        .order_by('-updated_at')
    )
    setores_por_nf = _setores_por_nf_ids_batch([c.nf_id for c in conferencias])

    historico = []
    for conferencia in conferencias:
        if busca:
            nf = conferencia.nf
            texto_busca = ' '.join(
                [
                    str(nf.numero or ''),
                    str(_nome_cliente_nf(nf) or ''),
                    str(nf.rota.nome or ''),
                    str(conferencia.status or ''),
                ]
            ).lower()
            if not _contains_busca(texto_busca, busca):
                continue
        if not pode_ver_todos:
            setores_nf = setores_por_nf.get(conferencia.nf_id, set())
            if not setores_nf.intersection(setores_usuario):
                continue

        nf = conferencia.nf
        data_ref = timezone.localtime(conferencia.updated_at).date()
        esperado = float(conferencia.total_esperado or 0)
        conferido = float(conferencia.total_conferido or 0)
        historico.append(
            {
                'id': nf.id,
                'numero': nf.numero,
                'cliente': _nome_cliente_nf(nf),
                'rota': f'Balcao - {nf.rota.nome}' if nf.balcao else nf.rota.nome,
                'status_fiscal': nf.status_fiscal,
                'status': conferencia.status,
                'status_separacao': 'SEPARADO',
                'conferencia_liberada': True,
                'conferencia_bloqueio_motivo': '',
                'balcao': nf.balcao,
                'updated_ts': nf.updated_at.timestamp(),
                'data_referencia': data_ref.isoformat(),
                'progresso': {
                    'esperado': esperado,
                    'conferido': conferido,
                    'percentual': 100.0 if esperado and conferido >= esperado else 0.0,
                },
                'itens_pendentes_conferencia': int(conferencia.pendentes or 0),
                'bloqueado': False,
                'usuario_em_uso': '',
                'em_uso_por_mim': False,
            }
        )
    return historico


def get_nfs_para_conferencia(usuario, data_inicio=None, data_fim=None, busca=None):
    busca = (busca or '').strip().lower()
    nfs = listar_nfs_disponiveis(usuario, somente_leitura=True)
    if not nfs:
        return []

    if not data_inicio and not data_fim and not busca:
        return nfs

    filtradas = []
    for nf in nfs:
        data_referencia_valor = nf.get('data_referencia') or ''
        if data_referencia_valor:
            data_ref = date.fromisoformat(data_referencia_valor)
        else:
            data_ref = timezone.localdate()
        if data_inicio and data_ref < data_inicio:
            continue
        if data_fim and data_ref > data_fim:
            continue
        if busca:
            texto_busca = ' '.join(
                [
                    str(nf.get('numero') or ''),
                    str(nf.get('cliente') or ''),
                    str(nf.get('rota') or ''),
                    str(nf.get('status') or ''),
                    str(nf.get('status_separacao') or ''),
                ]
            ).lower()
            if not _contains_busca(texto_busca, busca):
                continue
        filtradas.append(nf)

    logger.debug(
        'get_nfs_para_conferencia user=%s total=%s filtradas=%s',
        getattr(usuario, 'username', None),
        len(nfs),
        len(filtradas),
    )
    return filtradas


def get_nfs_monitoramento_conferencia(usuario, data_inicio=None, data_fim=None, busca=None):
    busca = (busca or '').strip().lower()
    cache_ttl = int(getattr(settings, 'DASHBOARD_CACHE_TTL', 15))
    cache_key = ':'.join(
        [
            'dashboard',
            'conferencia',
            f'v{_cache_version_monitoramento_conferencia()}',
            str(getattr(usuario, 'id', 'anon')),
            data_inicio.isoformat() if data_inicio else '',
            data_fim.isoformat() if data_fim else '',
            busca,
        ]
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    inicio = time.perf_counter()
    nfs_operacionais = get_nfs_para_conferencia(
        usuario,
        data_inicio=data_inicio,
        data_fim=data_fim,
        busca=busca,
    )
    ids_operacionais = {nf.get('id') for nf in nfs_operacionais if nf.get('id') is not None}
    historico = _carregar_historico_conferencia_dashboard(
        usuario,
        data_inicio=data_inicio,
        data_fim=data_fim,
        busca=busca,
        ids_operacionais=ids_operacionais,
    )

    combinado = list(nfs_operacionais) + historico
    combinado.sort(
        key=lambda nf: (
            0 if nf.get('balcao') else 1,
            str(nf.get('status', '')).upper() not in {'EM_CONFERENCIA', 'PENDENTE', 'EM CONFERENCIA'},
            -(nf.get('id') or 0),
        )
    )
    elapsed_ms = (time.perf_counter() - inicio) * 1000
    logger.info(
        'get_nfs_monitoramento_conferencia user=%s operacao=%s historico=%s total=%s tempo_ms=%.2f',
        getattr(usuario, 'username', None),
        len(nfs_operacionais),
        len(historico),
        len(combinado),
        elapsed_ms,
    )
    cache.set(cache_key, combinado, cache_ttl)
    return combinado


def get_tarefas_para_separacao(usuario, data_inicio=None, data_fim=None, busca=None):
    busca = (busca or '').strip().lower()
    tarefas = listar_tarefas_disponiveis(usuario)
    if not tarefas:
        return []

    if not data_inicio and not data_fim and not busca:
        return tarefas

    tarefa_ids = [t.get('id') for t in tarefas if t.get('id') is not None]
    tarefas_qs = (
        Tarefa.objects.select_related('nf', 'nf__cliente', 'rota')
        .defer('nf__bairro')
        .prefetch_related(Prefetch('itens'))
        .filter(id__in=tarefa_ids)
    )
    tarefa_por_id = {t.id: t for t in tarefas_qs}

    filtradas = []
    for tarefa in tarefas:
        tarefa_obj = tarefa_por_id.get(tarefa.get('id'))
        if tarefa_obj is None:
            continue
        data_ref = timezone.localtime(tarefa_obj.created_at).date() if tarefa_obj.created_at else timezone.localdate()
        if data_inicio and data_ref < data_inicio:
            continue
        if data_fim and data_ref > data_fim:
            continue
        if busca:
            texto_busca = ' '.join(
                [
                    str(tarefa.get('id') or ''),
                    str(tarefa.get('nf_numero') or ''),
                    str(tarefa.get('rota') or ''),
                    str(tarefa.get('setor') or ''),
                    str(tarefa.get('status') or ''),
                    str(getattr(tarefa_obj.nf.cliente, 'nome', '') if tarefa_obj.nf_id else ''),
                ]
            ).lower()
            if not _contains_busca(texto_busca, busca):
                continue
        filtradas.append(tarefa)

    logger.debug(
        'get_tarefas_para_separacao user=%s total=%s filtradas=%s',
        getattr(usuario, 'username', None),
        len(tarefas),
        len(filtradas),
    )
    return filtradas
