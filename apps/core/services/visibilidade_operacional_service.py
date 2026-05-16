import logging

from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Prefetch
from django.utils import timezone

from apps.conferencia.models import Conferencia, ConferenciaItem
from apps.conferencia.services.conferencia_service import listar_nfs_disponiveis
from apps.nf.models import NotaFiscal
from apps.produtos.models import Produto
from apps.tarefas.models import Tarefa
from apps.tarefas.services.separacao_service import listar_tarefas_disponiveis
from apps.usuarios.models import Setor

logger = logging.getLogger(__name__)
CONFERENCIA_MONITORAMENTO_CACHE_TTL = 60


def _nome_cliente_nf(nf):
    fallback = 'CLIENTE NAO INFORMADO'
    if nf is None or not getattr(nf, 'cliente_id', None):
        logger.info('NF sem cliente vinculado na conferencia nf_id=%s', getattr(nf, 'id', None))
        return fallback
    try:
        cliente = nf.cliente
    except ObjectDoesNotExist:
        logger.info(
            'NF sem cliente vinculado na conferencia nf_id=%s cliente_id=%s',
            getattr(nf, 'id', None),
            getattr(nf, 'cliente_id', None),
        )
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


def _setores_nf(nf):
    setores = set(
        nf.tarefas.exclude(setor='').values_list('setor', flat=True)
    )
    if setores:
        return setores
    for item in nf.itens.select_related('produto').all():
        if item.produto_id is None:
            setores.add(Setor.Codigo.NAO_ENCONTRADO)
            continue
        categoria = item.produto.categoria
        if categoria == Produto.Categoria.FILTROS:
            setores.add(Setor.Codigo.FILTROS)
        elif categoria == Produto.Categoria.LUBRIFICANTE:
            setores.add(Setor.Codigo.LUBRIFICANTE)
        elif categoria == Produto.Categoria.AGREGADO:
            setores.add(Setor.Codigo.AGREGADO)
        else:
            setores.add(Setor.Codigo.NAO_ENCONTRADO)
    return setores


def get_nfs_para_conferencia(usuario, data_inicio=None, data_fim=None, busca=None):
    busca = (busca or '').strip().lower()
    nfs = listar_nfs_disponiveis(usuario)
    if not nfs:
        return []

    if not data_inicio and not data_fim and not busca:
        return nfs

    nf_ids = [nf.get('id') for nf in nfs if nf.get('id') is not None]
    nf_por_id = {
        nf.id: nf
        for nf in NotaFiscal.objects.select_related('cliente', 'rota').defer('bairro').filter(id__in=nf_ids)
    }

    filtradas = []
    for nf in nfs:
        nf_obj = nf_por_id.get(nf.get('id'))
        if nf_obj is None:
            continue
        data_ref = timezone.localtime(nf_obj.created_at).date() if nf_obj.created_at else (
            nf_obj.data_emissao.date() if nf_obj.data_emissao else timezone.localdate()
        )
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
        'get_nfs_para_conferencia user=%s setores=%s total=%s filtradas=%s',
        getattr(usuario, 'username', None),
        list(usuario.setores.values_list('nome', flat=True)) if usuario else [],
        len(nfs),
        len(filtradas),
    )
    return filtradas


def get_nfs_monitoramento_conferencia(usuario, data_inicio=None, data_fim=None, busca=None):
    busca = (busca or '').strip().lower()
    cache_key = ':'.join(
        [
            'dashboard',
            'conferencia',
            str(getattr(usuario, 'id', 'anon')),
            data_inicio.isoformat() if data_inicio else '',
            data_fim.isoformat() if data_fim else '',
            busca,
        ]
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    nfs_operacionais = get_nfs_para_conferencia(
        usuario,
        data_inicio=data_inicio,
        data_fim=data_fim,
        busca=busca,
    )
    ids_operacionais = {nf.get('id') for nf in nfs_operacionais if nf.get('id') is not None}
    setores_usuario = _setores_usuario(usuario)
    pode_ver_todos = _usuario_pode_ver_todos_setores(usuario)
    if not pode_ver_todos and not setores_usuario:
        return nfs_operacionais

    nfs_historico_qs = (
        NotaFiscal.objects.select_related('cliente', 'rota')
        .defer('bairro')
        .prefetch_related('itens__produto', 'tarefas', 'conferencias__itens')
        .filter(ativa=True)
        .exclude(status_fiscal=NotaFiscal.StatusFiscal.CANCELADA)
        .filter(conferencias__status__in=[Conferencia.Status.OK, Conferencia.Status.CONCLUIDO_COM_RESTRICAO])
        .distinct()
        .order_by('-updated_at')
    )

    historico = []
    for nf in nfs_historico_qs:
        if nf.id in ids_operacionais:
            continue
        if not pode_ver_todos:
            if not _setores_nf(nf).intersection(setores_usuario):
                continue
        ultima_conferencia = nf.conferencias.exclude(status=Conferencia.Status.CANCELADA).order_by('-created_at').first()
        data_ref = timezone.localtime(ultima_conferencia.created_at).date() if ultima_conferencia else (
            timezone.localtime(nf.created_at).date() if nf.created_at else (
                nf.data_emissao.date() if nf.data_emissao else timezone.localdate()
            )
        )
        if data_inicio and data_ref < data_inicio:
            continue
        if data_fim and data_ref > data_fim:
            continue
        status = ultima_conferencia.status if ultima_conferencia else nf.status
        if busca:
            texto_busca = ' '.join(
                [
                    str(nf.numero or ''),
                    str(_nome_cliente_nf(nf) or ''),
                    str(nf.rota.nome or ''),
                    str(status or ''),
                ]
            ).lower()
            if not _contains_busca(texto_busca, busca):
                continue
        esperado = 0.0
        conferido = 0.0
        pendentes = 0
        if ultima_conferencia is not None:
            esperado = float(sum(item.qtd_esperada for item in ultima_conferencia.itens.all()))
            conferido = float(sum(item.qtd_conferida for item in ultima_conferencia.itens.all()))
            pendentes = ultima_conferencia.itens.exclude(status=ConferenciaItem.Status.OK).count()
        historico.append(
            {
                'id': nf.id,
                'numero': nf.numero,
                'cliente': _nome_cliente_nf(nf),
                'rota': f'Balcao - {nf.rota.nome}' if nf.balcao else nf.rota.nome,
                'status_fiscal': nf.status_fiscal,
                'status': status,
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
                'itens_pendentes_conferencia': pendentes,
                'bloqueado': False,
                'usuario_em_uso': '',
                'em_uso_por_mim': False,
            }
        )

    combinado = list(nfs_operacionais) + historico
    combinado.sort(key=lambda nf: (0 if nf.get('balcao') else 1, str(nf.get('status', '')).upper() not in {'EM_CONFERENCIA', 'PENDENTE'}, -(nf.get('id') or 0)))
    logger.debug(
        'get_nfs_monitoramento_conferencia user=%s total_operacao=%s total_historico=%s total_final=%s',
        getattr(usuario, 'username', None),
        len(nfs_operacionais),
        len(historico),
        len(combinado),
    )
    cache.set(cache_key, combinado, CONFERENCIA_MONITORAMENTO_CACHE_TTL)
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
        'get_tarefas_para_separacao user=%s setores=%s total=%s filtradas=%s',
        getattr(usuario, 'username', None),
        list(usuario.setores.values_list('nome', flat=True)) if usuario else [],
        len(tarefas),
        len(filtradas),
    )
    return filtradas
