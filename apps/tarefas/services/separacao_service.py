import time
import traceback
from datetime import timedelta
from decimal import Decimal
import logging

from django.conf import settings
from django.db import OperationalError, connection, transaction
from django.db.utils import ProgrammingError
from django.db.models import F, IntegerField, Max, Q, Sum
from django.db.models.functions import Cast
from django.utils import timezone

from apps.logs.models import Log, UserActivityLog
from apps.core.services.produto_validacao_service import (
    filtrar_queryset_por_codigo_produto,
    ProdutoValidacaoError,
    selecionar_item_por_codigo_lido,
    validar_produto,
)
from apps.nf.services.status_service import sincronizar_status_operacional_nfs
from apps.produtos.models import Produto
from apps.tarefas.models import Tarefa, TarefaItem
from apps.tarefas.services.onda_service import atualizar_progresso_bipagem, limpar_referencias_execucao_onda
from apps.usuarios.models import Setor, Usuario, UsuarioSessao


logger = logging.getLogger(__name__)


class SeparacaoError(Exception):
    pass


def _select_for_update_kwargs(*, nowait=True, skip_locked=False):
    """Lock apenas na tabela principal (sem OUTER JOIN no PostgreSQL)."""
    kwargs = {}
    if skip_locked:
        kwargs['skip_locked'] = True
    elif nowait:
        kwargs['nowait'] = True
    if connection.vendor == 'postgresql':
        kwargs['of'] = ('self',)
    return kwargs


def _tarefa_lock_queryset():
    from apps.tarefas.services.onda_schema import queryset_tarefa_lock

    return queryset_tarefa_lock(**_select_for_update_kwargs())


def _itens_pendentes_lock_queryset():
    return TarefaItem.objects.select_for_update(
        **_select_for_update_kwargs(skip_locked=True),
    ).select_related('produto')


def _identificador_tarefa_log(tarefa):
    from apps.nf.models import NotaFiscal
    from apps.rotas.models import Rota

    if tarefa.nf_id:
        numero = getattr(getattr(tarefa, 'nf', None), 'numero', None)
        if numero is None:
            numero = NotaFiscal.objects.filter(id=tarefa.nf_id).values_list('numero', flat=True).first()
        return f'NF {numero}'
    nome = getattr(getattr(tarefa, 'rota', None), 'nome', None)
    if nome is None:
        nome = Rota.objects.filter(id=tarefa.rota_id).values_list('nome', flat=True).first()
    return f'rota {nome}'


def _obter_tarefa_ou_erro(queryset, tarefa_id):
    tarefa = queryset.filter(id=tarefa_id).first()
    if tarefa is None:
        raise SeparacaoError('Tarefa nao encontrada')
    return tarefa

NF_CANCELADA_ERRO = 'NF cancelada não pode ser processada'
TAREFA_SETOR_ERRO = 'Tarefa não pertence ao setor do usuário'
USUARIO_SEM_SETOR_ERRO = 'Usuário sem setor vinculado. Contate o administrador.'
FINALIZACAO_FILTRO_PENDENTE_ERRO = 'NF de filtros com item faltante nao pode ser finalizada'
TAREFA_EM_EXECUCAO_ERRO = 'Tarefa ja esta em execucao por outro usuario'
TAREFA_NAO_ACEITA_ERRO = 'Aceite a tarefa antes de iniciar a bipagem'


STATUS_TAREFA_DISPONIVEL = (Tarefa.Status.ABERTO, Tarefa.Status.EM_EXECUCAO)
SQLITE_LOCK_RETRY_MAX = 3
SQLITE_LOCK_RETRY_DELAY_BASE_SECONDS = 0.12


def _registrar_log_seguro(usuario, acao, detalhe):
    try:
        Log.objects.create(usuario=usuario, acao=acao, detalhe=detalhe)
    except Exception as exc:
        print(f'ERRO LOG SEPARACAO: {exc}')
        traceback.print_exc()


def _registrar_atividade_segura(usuario, tipo, tarefa, timestamp):
    try:
        UserActivityLog.objects.create(
            usuario=usuario,
            tipo=tipo,
            tarefa=tarefa,
            timestamp=timestamp,
        )
    except Exception as exc:
        print(f'ERRO ATIVIDADE SEPARACAO: {exc}')
        traceback.print_exc()


SETOR_CATEGORIA_MAP = {
    Setor.Codigo.LUBRIFICANTE: Produto.Categoria.LUBRIFICANTE,
    Setor.Codigo.AGREGADO: Produto.Categoria.AGREGADO,
    Setor.Codigo.FILTROS: Produto.Categoria.FILTROS,
    Setor.Codigo.NAO_ENCONTRADO: Produto.Categoria.NAO_ENCONTRADO,
}


def _normalizar_setor_operacional(valor):
    setor = (valor or '').strip().upper()
    if setor == 'FILTRO':
        return Setor.Codigo.FILTROS
    if setor == 'NAO ENCONTRADO':
        return Setor.Codigo.NAO_ENCONTRADO
    return setor


def _usuario_pode_ver_todos_setores(usuario):
    return bool(getattr(usuario, 'is_superuser', False))


def _setores_usuario(usuario):
    from apps.core.operacional_cache import setores_usuario_operacional

    setores = setores_usuario_operacional(usuario)
    if setores is None:
        return set()
    return setores


def _erro_schema_onda_listagem(exc):
    mensagem = str(exc).lower()
    marcadores = (
        'tarefas_ondaseparacao',
        'onda_id',
        'tipo_embalagem',
        'itens_total',
        'itens_bipados',
        'itens_pendentes',
        'percentual',
    )
    return any(marcador in mensagem for marcador in marcadores)


def _queryset_tarefas_disponiveis_com_onda():
    return (
        Tarefa.objects.select_related('nf', 'rota', 'usuario', 'usuario_em_execucao', 'onda')
        .defer('nf__bairro')
        .prefetch_related('itens__produto', 'itens__nf')
        .filter(ativo=True)
        .filter(status__in=[Tarefa.Status.ABERTO, Tarefa.Status.EM_EXECUCAO])
        .filter(Q(nf__isnull=True) | ~Q(nf__status_fiscal='CANCELADA'))
        .order_by('-id')
    )


def _queryset_tarefas_disponiveis_classico():
    return (
        Tarefa.objects.select_related('nf', 'rota', 'usuario', 'usuario_em_execucao')
        .only(
            'id',
            'created_at',
            'updated_at',
            'tipo',
            'setor',
            'nf_id',
            'rota_id',
            'usuario_id',
            'usuario_em_execucao_id',
            'data_inicio',
            'status',
            'ativo',
            'nf__id',
            'nf__numero',
            'nf__status_fiscal',
            'nf__balcao',
            'rota__id',
            'rota__nome',
            'usuario__id',
            'usuario__nome',
            'usuario__username',
            'usuario_em_execucao__id',
            'usuario_em_execucao__nome',
            'usuario_em_execucao__username',
        )
        .prefetch_related('itens__produto', 'itens__nf')
        .filter(ativo=True)
        .filter(status__in=[Tarefa.Status.ABERTO, Tarefa.Status.EM_EXECUCAO])
        .filter(Q(nf__isnull=True) | ~Q(nf__status_fiscal='CANCELADA'))
        .order_by('-id')
    )


def _aplicar_defaults_fallback_onda(tarefas):
    for tarefa in tarefas:
        tarefa.__dict__['onda_id'] = None
        tarefa.__dict__['onda'] = None
        tarefa.__dict__['tipo_embalagem'] = ''
        tarefa.__dict__['itens_total'] = Decimal('0')
        tarefa.__dict__['itens_bipados'] = Decimal('0')
        tarefa.__dict__['itens_pendentes'] = Decimal('0')
        tarefa.__dict__['percentual'] = Decimal('0')
    return tarefas


def listar_tarefas_disponiveis(usuario=None, *, data_inicio=None, data_fim=None, path='/separacao/'):
    from apps.tarefas.services.onda_schema import schema_onda_disponivel

    if not schema_onda_disponivel():
        queryset = _queryset_tarefas_disponiveis_classico()
        if data_inicio is not None:
            queryset = queryset.filter(
                Q(created_at__date__gte=data_inicio) | Q(updated_at__date__gte=data_inicio)
            )
        if data_fim is not None:
            queryset = queryset.filter(
                Q(created_at__date__lte=data_fim) | Q(updated_at__date__lte=data_fim)
            )
        queryset = _filtrar_tarefas_por_setor(queryset, usuario)
        tarefas = _aplicar_defaults_fallback_onda(list(queryset))
        _normalizar_tarefas_lista_operacional(tarefas, usuario)
        tarefas = [tarefa for tarefa in tarefas if tarefa.status in STATUS_TAREFA_DISPONIVEL]
        tarefas_ordenadas = sorted(
            tarefas,
            key=lambda tarefa: (
                0 if (_tarefa_balcao(tarefa) and tarefa.status == Tarefa.Status.ABERTO) else 1,
                0 if tarefa.status == Tarefa.Status.ABERTO else 1,
                tarefa.id,
            ),
        )
        return [_serializar_tarefa_lista(tarefa, usuario) for tarefa in tarefas_ordenadas]

    queryset = _queryset_tarefas_disponiveis_com_onda()
    if data_inicio is not None:
        queryset = queryset.filter(
            Q(created_at__date__gte=data_inicio) | Q(updated_at__date__gte=data_inicio)
        )
    if data_fim is not None:
        queryset = queryset.filter(
            Q(created_at__date__lte=data_fim) | Q(updated_at__date__lte=data_fim)
        )
    queryset = _filtrar_tarefas_por_setor(queryset, usuario)
    try:
        tarefas = list(queryset)
    except ProgrammingError as exc:
        if not _erro_schema_onda_listagem(exc):
            raise
        logger.exception(
            'ONDA_LISTAGEM_FALLBACK exception=%s user_id=%s path=%s modo=classico',
            exc.__class__.__name__,
            getattr(usuario, 'id', None),
            path,
        )
        queryset = _queryset_tarefas_disponiveis_classico()
        if data_inicio is not None:
            queryset = queryset.filter(
                Q(created_at__date__gte=data_inicio) | Q(updated_at__date__gte=data_inicio)
            )
        if data_fim is not None:
            queryset = queryset.filter(
                Q(created_at__date__lte=data_fim) | Q(updated_at__date__lte=data_fim)
            )
        queryset = _filtrar_tarefas_por_setor(queryset, usuario)
        tarefas = _aplicar_defaults_fallback_onda(list(queryset))
    _normalizar_tarefas_lista_operacional(tarefas, usuario)

    tarefas = [tarefa for tarefa in tarefas if tarefa.status in STATUS_TAREFA_DISPONIVEL]
    tarefas_ordenadas = sorted(
        tarefas,
        key=lambda tarefa: (
            0 if (_tarefa_balcao(tarefa) and tarefa.status == Tarefa.Status.ABERTO) else 1,
            0 if tarefa.status == Tarefa.Status.ABERTO else 1,
            tarefa.id,
        ),
    )
    logger.info(
        'FILTRO_DEBUG user_id=%s setores_usuario=%s filtros_aplicados=%s queryset_final_count=%s',
        getattr(usuario, 'id', None),
        sorted(_setores_usuario(usuario)) if usuario is not None and _setores_usuario(usuario) else [],
        'tarefas.setor__in' if usuario is not None and not _usuario_pode_ver_todos_setores(usuario) else 'sem_restricao',
        len(tarefas_ordenadas),
    )
    return [_serializar_tarefa_lista(tarefa, usuario) for tarefa in tarefas_ordenadas]


def _normalizar_tarefas_lista_operacional(tarefas, usuario=None):
    """Conclusão automática e liberação de tarefas órfãs sem N saves na listagem."""
    if not tarefas:
        return
    limite_online = timezone.now() - timedelta(minutes=5)
    responsaveis_ids = {
        tarefa.usuario_em_execucao_id or tarefa.usuario_id
        for tarefa in tarefas
        if (tarefa.usuario_em_execucao_id or tarefa.usuario_id)
    }
    online_ids = set()
    if responsaveis_ids:
        online_ids = set(
            UsuarioSessao.objects.filter(
                usuario_id__in=responsaveis_ids,
                ativo=True,
                ultimo_acesso__gte=limite_online,
            ).values_list('usuario_id', flat=True)
        )

    ids_reabrir = []
    ids_limpar_responsavel = []
    agora = timezone.now()
    for tarefa in tarefas:
        if sincronizar_conclusao_automatica_tarefa(tarefa, usuario):
            continue
        if tarefa.status == Tarefa.Status.CONCLUIDO:
            continue
        responsavel_id = tarefa.usuario_em_execucao_id or tarefa.usuario_id
        if responsavel_id and responsavel_id not in online_ids:
            if tarefa.status == Tarefa.Status.EM_EXECUCAO:
                ids_reabrir.append(tarefa.id)
                tarefa.status = Tarefa.Status.ABERTO
            else:
                ids_limpar_responsavel.append(tarefa.id)
            tarefa.usuario_id = None
            tarefa.usuario_em_execucao_id = None
            tarefa.data_inicio = None
            continue
        if tarefa.status == Tarefa.Status.EM_EXECUCAO and tarefa.usuario_em_execucao_id is None:
            ids_reabrir.append(tarefa.id)
            tarefa.status = Tarefa.Status.ABERTO
            tarefa.usuario_id = None
            tarefa.data_inicio = None

    if ids_reabrir:
        Tarefa.objects.filter(id__in=ids_reabrir).update(
            status=Tarefa.Status.ABERTO,
            usuario_id=None,
            usuario_em_execucao_id=None,
            data_inicio=None,
            updated_at=agora,
        )
    if ids_limpar_responsavel:
        Tarefa.objects.filter(id__in=ids_limpar_responsavel).update(
            usuario_id=None,
            usuario_em_execucao_id=None,
            data_inicio=None,
            updated_at=agora,
        )


def _serializar_tarefa_lista(tarefa, usuario=None):
    return {
        'id': tarefa.id,
        'nf_id': tarefa.nf_id,
        'nf_numero': _nf_tarefa_resumo(tarefa),
        'tipo': tarefa.tipo,
        'status': tarefa.status,
        'rota': f'Balcao - {tarefa.rota.nome}' if _tarefa_balcao(tarefa) else tarefa.rota.nome,
        'setor': tarefa.setor,
        'segmento': _segmento_tarefa(tarefa),
        'operacao': 'NF' if tarefa.tipo == Tarefa.Tipo.FILTRO else 'ROTA',
        'usuario_id': tarefa.usuario_em_execucao_id or tarefa.usuario_id,
        'usuario_nome': (
            tarefa.usuario_em_execucao.nome
            if tarefa.usuario_em_execucao_id and tarefa.usuario_em_execucao
            else (tarefa.usuario.nome if tarefa.usuario_id and tarefa.usuario else '')
        ),
        'onda_codigo': tarefa.onda.codigo if tarefa.onda_id and getattr(tarefa, 'onda', None) else '',
        'onda_status': tarefa.onda.status if tarefa.onda_id and getattr(tarefa, 'onda', None) else '',
        'tipo_embalagem': tarefa.tipo_embalagem or '',
        'onda_nf_total': tarefa.onda.nf_total if tarefa.onda_id and getattr(tarefa, 'onda', None) else (1 if tarefa.nf_id else 0),
        'itens_total': float(tarefa.itens_total or 0),
        'itens_bipados': float(tarefa.itens_bipados or 0),
        'itens_pendentes': float(tarefa.itens_pendentes or 0),
        'percentual': float(tarefa.percentual or 0),
        'bloqueado': bool(
            usuario is not None
            and (tarefa.usuario_em_execucao_id or tarefa.usuario_id)
            and (tarefa.usuario_em_execucao_id or tarefa.usuario_id) != usuario.id
        ),
        'em_uso_por_mim': bool(usuario is not None and (tarefa.usuario_em_execucao_id or tarefa.usuario_id) == usuario.id),
        'balcao': _tarefa_balcao(tarefa),
    }


def obter_proxima_tarefa_separacao(usuario, *, excluir_tarefa_id=None):
    """Próxima tarefa ativa no período operacional (consulta leve, sem histórico)."""
    from apps.core.operacional_periodo import periodo_operacional_padrao

    data_inicio, data_fim = periodo_operacional_padrao()
    queryset = (
        Tarefa.objects.filter(ativo=True)
        .filter(status__in=STATUS_TAREFA_DISPONIVEL)
        .filter(Q(nf__isnull=True) | ~Q(nf__status_fiscal='CANCELADA'))
        .filter(Q(created_at__date__gte=data_inicio) | Q(updated_at__date__gte=data_inicio))
        .filter(Q(created_at__date__lte=data_fim) | Q(updated_at__date__lte=data_fim))
    )
    queryset = _filtrar_tarefas_por_setor(queryset, usuario)
    if excluir_tarefa_id:
        queryset = queryset.exclude(id=excluir_tarefa_id)
    candidatas = list(
        queryset.only('id', 'status', 'setor', 'tipo', 'nf_id', 'rota_id', 'usuario_id', 'usuario_em_execucao_id')
        .select_related('nf', 'rota')
        .order_by('-id')[:80]
    )
    disponiveis = []
    for tarefa in candidatas:
        if tarefa.status == Tarefa.Status.EM_EXECUCAO:
            responsavel_id = tarefa.usuario_em_execucao_id or tarefa.usuario_id
            if responsavel_id and responsavel_id != getattr(usuario, 'id', None):
                continue
        disponiveis.append(tarefa)
    if not disponiveis:
        return None
    disponiveis.sort(
        key=lambda tarefa: (
            0 if (_tarefa_balcao(tarefa) and tarefa.status == Tarefa.Status.ABERTO) else 1,
            0 if tarefa.status == Tarefa.Status.ABERTO else 1,
            tarefa.id,
        )
    )
    escolhida = disponiveis[0]
    return {'id': escolhida.id}


def status_item_tarefa(tarefa_status, quantidade_separada, quantidade_total, possui_restricao=False):
    if possui_restricao and tarefa_status == Tarefa.Status.FECHADO_COM_RESTRICAO:
        return 'COM RESTRICAO'
    if possui_restricao and tarefa_status == Tarefa.Status.LIBERADO_COM_RESTRICAO:
        return 'LIBERADO COM RESTRICAO'
    if possui_restricao and tarefa_status == Tarefa.Status.CONCLUIDO_COM_RESTRICAO:
        return 'CONCLUIDO COM RESTRICAO'
    if tarefa_status == Tarefa.Status.CONCLUIDO or quantidade_separada >= quantidade_total:
        return 'SEPARADO'
    if tarefa_status == Tarefa.Status.EM_EXECUCAO:
        return 'EM EXECUCAO'
    if quantidade_separada > 0:
        return 'EM EXECUCAO'
    return 'AGUARDANDO'


def listar_itens_tarefa_para_exibicao(tarefa):
    itens_queryset = TarefaItem.objects.filter(tarefa=tarefa)

    itens_agrupados = list(
        itens_queryset.filter(
            produto__categoria__in=[
                Produto.Categoria.LUBRIFICANTE,
                Produto.Categoria.AGREGADO,
                Produto.Categoria.NAO_ENCONTRADO,
            ]
        )
        .values(
            'produto__cod_prod',
            'produto__descricao',
            'produto__setor',
            'produto__categoria',
            'grupo_agregado__nome',
            'tarefa__rota_id',
            'tarefa__rota__nome',
        )
        .annotate(
            quantidade_total=Sum('quantidade_total'),
            quantidade_separada=Sum('quantidade_separada'),
            possui_restricao=Max(Cast('possui_restricao', IntegerField())),
            data_bipagem=Max('data_bipagem'),
            bipado_por_nome=Max('bipado_por__nome'),
            bipado_por_username=Max('bipado_por__username'),
        )
        .order_by('produto__cod_prod')
    )

    itens_filtros = list(
        itens_queryset.filter(produto__categoria=Produto.Categoria.FILTROS)
        .values(
            'produto__cod_prod',
            'produto__descricao',
            'produto__setor',
            'produto__categoria',
            'grupo_agregado__nome',
            'tarefa__rota_id',
            'tarefa__rota__nome',
            'nf_id',
            'nf__numero',
            'quantidade_total',
            'quantidade_separada',
            'possui_restricao',
            'data_bipagem',
            'bipado_por__nome',
            'bipado_por__username',
        )
        .order_by('produto__cod_prod', 'nf__numero', 'id')
    )

    linhas = []
    for item in itens_agrupados:
        quantidade_total = item['quantidade_total'] or Decimal('0')
        quantidade_separada = item['quantidade_separada'] or Decimal('0')
        possui_restricao = bool(item['possui_restricao'])
        linhas.append(
            {
                'produto': item['produto__cod_prod'],
                'descricao': item['produto__descricao'],
                'setor': item.get('produto__setor') or '',
                'grupo_agregado': item.get('grupo_agregado__nome') or '',
                'categoria': item['produto__categoria'],
                'rota': item['tarefa__rota__nome'],
                'nf_numero': None,
                'agrupado': True,
                'quantidade_total': quantidade_total,
                'quantidade_separada': quantidade_separada,
                'status': status_item_tarefa(tarefa.status, quantidade_separada, quantidade_total, possui_restricao),
                'bipado_por': item.get('bipado_por_nome') or item.get('bipado_por_username') or '',
                'data_bipagem': item.get('data_bipagem'),
            }
        )

    for item in itens_filtros:
        quantidade_total = item['quantidade_total'] or Decimal('0')
        quantidade_separada = item['quantidade_separada'] or Decimal('0')
        possui_restricao = bool(item['possui_restricao'])
        linhas.append(
            {
                'produto': item['produto__cod_prod'],
                'descricao': item['produto__descricao'],
                'setor': item.get('produto__setor') or '',
                'grupo_agregado': item.get('grupo_agregado__nome') or '',
                'categoria': item['produto__categoria'],
                'rota': item['tarefa__rota__nome'],
                'nf_numero': item['nf__numero'],
                'agrupado': False,
                'quantidade_total': quantidade_total,
                'quantidade_separada': quantidade_separada,
                'status': status_item_tarefa(tarefa.status, quantidade_separada, quantidade_total, possui_restricao),
                'bipado_por': item.get('bipado_por__nome') or item.get('bipado_por__username') or '',
                'data_bipagem': item.get('data_bipagem'),
            }
        )

    return linhas


def listar_itens_tarefa_para_exibicao_seguro(tarefa):
    try:
        return listar_itens_tarefa_para_exibicao(tarefa)
    except Exception as exc:
        logger.exception('ERRO_ITENS_SEPARACAO tarefa_id=%s erro=%s', getattr(tarefa, 'id', None), str(exc))

    itens_rel = getattr(tarefa, 'itens', None)
    itens = itens_rel.select_related('produto', 'nf', 'grupo_agregado', 'tarefa__rota').all() if itens_rel else []
    linhas = []
    for item in itens:
        produto = getattr(item, 'produto', None)
        quantidade_total = getattr(item, 'quantidade_total', None) or Decimal('0')
        quantidade_separada = getattr(item, 'quantidade_separada', None) or Decimal('0')
        possui_restricao = bool(getattr(item, 'possui_restricao', False))
        linhas.append(
            {
                'produto': getattr(produto, 'cod_prod', '') or '',
                'descricao': getattr(produto, 'descricao', '') or '',
                'setor': getattr(produto, 'setor', '') or '',
                'grupo_agregado': getattr(getattr(item, 'grupo_agregado', None), 'nome', '') or '',
                'categoria': getattr(produto, 'categoria', '') or '',
                'rota': getattr(getattr(tarefa, 'rota', None), 'nome', '') or '',
                'nf_numero': getattr(getattr(item, 'nf', None), 'numero', None),
                'agrupado': False,
                'quantidade_total': quantidade_total,
                'quantidade_separada': quantidade_separada,
                'status': status_item_tarefa(tarefa.status, quantidade_separada, quantidade_total, possui_restricao),
                'bipado_por': (
                    getattr(getattr(item, 'bipado_por', None), 'nome', None)
                    or getattr(getattr(item, 'bipado_por', None), 'username', '')
                    or ''
                ),
                'data_bipagem': getattr(item, 'data_bipagem', None),
            }
        )
    return linhas


def iniciar_tarefa(tarefa_id, usuario):
    try:
        logger.info('SEPARACAO_INICIAR_START tarefa_id=%s user_id=%s', tarefa_id, getattr(usuario, 'id', None))
        from apps.core.operacional_cache import usuario_tem_setor_vinculado

        if usuario is None or not usuario_tem_setor_vinculado(usuario):
            raise SeparacaoError(USUARIO_SEM_SETOR_ERRO)

        from apps.tarefas.services.onda_schema import queryset_tarefa_web

        tarefa = _obter_tarefa_ou_erro(queryset_tarefa_web(prefetch_itens_nf=True), tarefa_id)
        _validar_nf_cancelada(tarefa, usuario, 'SEPARACAO BLOQUEADA')
        _validar_setor_tarefa(tarefa, usuario)
        _validar_execucao_tarefa(tarefa, usuario, exigir_aceite=False)

        def _executar():
            with transaction.atomic():
                tarefa = _obter_tarefa_ou_erro(
                    _tarefa_lock_queryset(),
                    tarefa_id,
                )
                _validar_nf_cancelada(tarefa, usuario, 'SEPARACAO BLOQUEADA')
                _validar_setor_tarefa(tarefa, usuario)
                _validar_execucao_tarefa(tarefa, usuario, exigir_aceite=False)
                if tarefa.status in {Tarefa.Status.FECHADO_COM_RESTRICAO, Tarefa.Status.LIBERADO_COM_RESTRICAO, Tarefa.Status.CONCLUIDO_COM_RESTRICAO}:
                    raise SeparacaoError('Tarefa com restricao nao pode ser reiniciada')
                tarefa.status = Tarefa.Status.EM_EXECUCAO
                tarefa.usuario = usuario
                tarefa.usuario_em_execucao = usuario
                tarefa.data_inicio = timezone.now()
                tarefa.save(update_fields=['status', 'usuario', 'usuario_em_execucao', 'data_inicio', 'updated_at'])
                from apps.tarefas.services.onda_schema import schema_onda_disponivel

                if schema_onda_disponivel() and getattr(tarefa, 'onda_id', None):
                    tarefa.onda.operador_id = usuario.id
                    tarefa.onda.status = tarefa.onda.Status.EM_SEPARACAO
                    tarefa.onda.save(update_fields=['operador', 'status', 'updated_at'])
                identificador = _identificador_tarefa_log(tarefa)
                _registrar_log_seguro(usuario, 'INICIO SEPARACAO', f'Tarefa {tarefa.id} iniciada para {identificador}.')
                _registrar_atividade_segura(usuario, UserActivityLog.Tipo.TAREFA_INICIO, tarefa, timezone.now())
                return tarefa

        tarefa = _executar_com_retry_sqlite_lock(_executar)
        from apps.core.operacional_sessao_cache import preload_mapa_bipagem_separacao

        preload_mapa_bipagem_separacao(tarefa.id)
        return _dados_tarefa(tarefa)
    except Exception as exc:
        logger.exception('SEPARACAO_INICIAR_FALHA tarefa_id=%s user_id=%s erro=%s', tarefa_id, getattr(usuario, 'id', None), str(exc))
        raise


def _montar_resposta_bipagem_separacao(tarefa, item, *, finalizado=False):
    status_tarefa = tarefa.status
    if finalizado:
        status_tarefa = Tarefa.Status.CONCLUIDO
    resposta = {
        'status': 'ok',
        'esperado': float(item.quantidade_total),
        'separado': float(item.quantidade_separada),
        'status_tarefa': status_tarefa,
        'finalizado': finalizado,
        'produto_cod': getattr(item.produto, 'cod_prod', '') or '',
        'onda_codigo': tarefa.onda.codigo if getattr(tarefa, 'onda_id', None) and getattr(tarefa, 'onda', None) else '',
        'tipo_embalagem': getattr(tarefa, 'tipo_embalagem', '') or '',
        'feedback': f'Produto validado no setor {(item.produto.setor or "").strip().upper() or "-"}',
        'cor': 'verde',
        'som': 'beep-curto',
    }
    return resposta


def _resposta_bipagem_duplicada_separacao(tarefa_id, codigo):
    from apps.core.operacional_sessao_cache import resolver_item_id_separacao

    item_id, _ = resolver_item_id_separacao(tarefa_id, codigo)
    if not item_id:
        return None
    item = (
        TarefaItem.objects.filter(pk=item_id)
        .select_related('produto', 'tarefa', 'tarefa__onda')
        .first()
    )
    if item is None:
        return None
    return _montar_resposta_bipagem_separacao(item.tarefa, item, finalizado=False)


def bipar_tarefa(tarefa_id, codigo, usuario):
    from apps.core.bipagem_leitura import eh_bipagem_duplicada, sanitizar_entrada_scanner
    from apps.core.operacional_bipagem_metrics import BipagemMetrics
    from apps.core.operacional_sessao_cache import (
        atualizar_mapa_apos_bipagem_separacao,
        invalidar_mapa_separacao,
        resolver_item_id_separacao,
    )
    from apps.core.operacional_side_effects import (
        agendar_conclusao_automatica_separacao,
        agendar_invalidacao_operacional,
        agendar_logs_bipagem_separacao,
        agendar_nf_ids_separacao,
    )
    from apps.tarefas.services.onda_schema import queryset_tarefa_bipagem_lock, schema_onda_disponivel

    codigo = sanitizar_entrada_scanner(codigo)
    metricas = BipagemMetrics('separacao', tarefa_id, getattr(usuario, 'id', None))
    try:
        if eh_bipagem_duplicada(modulo='separacao', entidade_id=tarefa_id, usuario_id=usuario.id, codigo=codigo):
            metricas.duplicada = True
            with metricas.fase('response'):
                resposta_dup = _resposta_bipagem_duplicada_separacao(tarefa_id, codigo)
                if resposta_dup:
                    return resposta_dup

        with metricas.fase('cache'):
            item_id_cache, cache_hit = resolver_item_id_separacao(tarefa_id, codigo)
            metricas.cache_hit = cache_hit

        pendente_side_effects = None

        def _executar():
            nonlocal pendente_side_effects
            tarefa_local = None
            item_local = None
            finalizado_local = False
            nf_ids = []
            onda_payload = None

            with transaction.atomic():
                with metricas.fase('lock'):
                    tarefa_local = queryset_tarefa_bipagem_lock(
                        tarefa_id=tarefa_id,
                        select_for_update_kwargs=_select_for_update_kwargs(),
                    )
                    _validar_nf_cancelada(tarefa_local, usuario, 'SEPARACAO BLOQUEADA')
                    _validar_setor_tarefa(tarefa_local, usuario)
                    _validar_execucao_tarefa(tarefa_local, usuario)
                    if tarefa_local.status == Tarefa.Status.CONCLUIDO:
                        raise SeparacaoError('Tarefa já concluída.')

                with metricas.fase('query'):
                    itens_pendentes_qs = (
                        TarefaItem.objects.filter(
                            tarefa_id=tarefa_id,
                            quantidade_separada__lt=F('quantidade_total'),
                        )
                        .select_related('produto')
                        .only(
                            'id',
                            'tarefa_id',
                            'nf_id',
                            'produto_id',
                            'quantidade_total',
                            'quantidade_separada',
                            'possui_restricao',
                            'produto__id',
                            'produto__cod_prod',
                            'produto__cod_ean',
                            'produto__codigo',
                            'produto__setor',
                            'produto__categoria',
                        )
                        .order_by('nf_id', 'created_at')
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
                        raise SeparacaoError('Tarefa sem itens pendentes para bipagem')
                    try:
                        validacao = validar_produto(
                            codigo_lido=codigo,
                            item_id=item_esperado.id,
                            usuario=usuario,
                            item_model=TarefaItem,
                            tipo_validacao='SEPARACAO',
                            item_travado=item_esperado,
                        )
                    except ProdutoValidacaoError as exc:
                        raise SeparacaoError(str(exc)) from exc

                    _validar_produto_no_setor(
                        item=validacao.item,
                        produto=validacao.item.produto,
                        usuario=usuario,
                        codigo_lido=codigo,
                    )

                with metricas.fase('lock'):
                    item_local = (
                        TarefaItem.objects.select_for_update(**_select_for_update_kwargs())
                        .select_related('produto')
                        .only(
                            'id',
                            'tarefa_id',
                            'nf_id',
                            'produto_id',
                            'quantidade_total',
                            'quantidade_separada',
                            'possui_restricao',
                            'produto__id',
                            'produto__cod_prod',
                            'produto__cod_ean',
                            'produto__codigo',
                            'produto__setor',
                            'produto__categoria',
                        )
                        .get(pk=validacao.item.id)
                    )
                    if item_local.quantidade_separada >= item_local.quantidade_total:
                        raise SeparacaoError('Quantidade excedida')
                    nova_separada = item_local.quantidade_separada + Decimal('1')
                    completo = nova_separada >= item_local.quantidade_total
                    agora = timezone.now()

                with metricas.fase('save'):
                    TarefaItem.objects.filter(pk=item_local.pk).update(
                        quantidade_separada=nova_separada,
                        bipado_por_id=usuario.id,
                        data_bipagem=agora,
                        possui_restricao=False if completo else item_local.possui_restricao,
                        updated_at=agora,
                    )
                    item_local.quantidade_separada = nova_separada

                pendentes_antes = getattr(tarefa_local, 'itens_pendentes', None) or Decimal('0')
                itens_total_tarefa = getattr(tarefa_local, 'itens_total', None)
                if completo and itens_total_tarefa and pendentes_antes > 0:
                    finalizado_local = pendentes_antes <= Decimal('1')
                elif completo:
                    finalizado_local = not TarefaItem.objects.filter(
                        tarefa_id=tarefa_id,
                        quantidade_separada__lt=F('quantidade_total'),
                    ).exclude(pk=item_local.pk).exists()

                if tarefa_local.nf_id:
                    nf_ids.append(tarefa_local.nf_id)
                if item_local.nf_id and item_local.nf_id not in nf_ids:
                    nf_ids.append(item_local.nf_id)

                if schema_onda_disponivel():
                    onda_payload = {
                        'tarefa_id': tarefa_local.id,
                        'onda_id': getattr(tarefa_local, 'onda_id', None),
                        'operador_id': usuario.id,
                        'finalizado': finalizado_local,
                    }

            pendente_side_effects = {
                'tarefa_id': tarefa_local.id,
                'produto_cod': item_local.produto.cod_prod,
                'finalizado': finalizado_local,
                'nf_ids': nf_ids,
                'onda': onda_payload,
                'item_id': item_local.id,
                'codigo': codigo,
            }
            return tarefa_local, item_local, finalizado_local

        tarefa, item, finalizado = _executar_com_retry_sqlite_lock(_executar)

        if pendente_side_effects:
            payload = pendente_side_effects

            def _pos_commit_side_effects():
                try:
                    if payload.get('onda') and schema_onda_disponivel():
                        onda = payload['onda']
                        atualizar_progresso_bipagem(
                            tarefa_id=onda['tarefa_id'],
                            onda_id=onda['onda_id'],
                            operador_id=onda['operador_id'],
                            delta=Decimal('0') if onda['finalizado'] else Decimal('1'),
                            finalizado=onda['finalizado'],
                        )
                    agendar_logs_bipagem_separacao(
                        usuario_id=usuario.id,
                        tarefa_id=payload['tarefa_id'],
                        produto_cod=payload['produto_cod'],
                        finalizacao_automatica=payload['finalizado'],
                    )
                    if payload['finalizado']:
                        agendar_conclusao_automatica_separacao(
                            tarefa_id=payload['tarefa_id'],
                            usuario_id=usuario.id,
                        )
                        agendar_invalidacao_operacional(motivo='bipagem_separacao_finalizada')
                        invalidar_mapa_separacao(tarefa_id)
                    else:
                        agendar_nf_ids_separacao(payload['nf_ids'])
                        atualizar_mapa_apos_bipagem_separacao(
                            tarefa_id,
                            payload['item_id'],
                            payload['codigo'],
                        )
                except Exception as exc:
                    logger.warning(
                        'ASYNC_SIDE_EFFECT falha modulo=separacao tarefa_id=%s erro=%s',
                        payload['tarefa_id'],
                        exc,
                    )

            transaction.on_commit(_pos_commit_side_effects)

        with metricas.fase('response'):
            with metricas.fase('serialize'):
                resposta = _montar_resposta_bipagem_separacao(tarefa, item, finalizado=finalizado)
                if finalizado:
                    from apps.core.operacional_transicao import anexar_transicao_separacao

                    anexar_transicao_separacao(resposta, usuario, tarefa_id_atual=tarefa.id)
            return resposta
    finally:
        metricas.registrar()


def finalizar_tarefa(tarefa_id, status, usuario, motivo=None):
    logger.info(
        'SEPARACAO_FINALIZAR_START tarefa_id=%s user_id=%s status=%s motivo=%s',
        tarefa_id,
        getattr(usuario, 'id', None),
        status,
        bool((motivo or '').strip()),
    )
    from apps.tarefas.services.onda_schema import queryset_tarefa_web

    tarefa = queryset_tarefa_web(prefetch_itens_nf=True).get(id=tarefa_id)
    _validar_nf_cancelada(tarefa, usuario, 'SEPARACAO BLOQUEADA')
    _validar_setor_tarefa(tarefa, usuario)
    _validar_execucao_tarefa(tarefa, usuario)
    if status not in {Tarefa.Status.CONCLUIDO, Tarefa.Status.FECHADO_COM_RESTRICAO, Tarefa.Status.CONCLUIDO_COM_RESTRICAO}:
        raise SeparacaoError('Status de finalização inválido')
    possui_pendencia = TarefaItem.objects.filter(
        tarefa_id=tarefa.id,
        quantidade_separada__lt=F('quantidade_total'),
    ).exists()
    tarefa_liberada = tarefa.status == Tarefa.Status.LIBERADO_COM_RESTRICAO
    status_final = status
    if status == Tarefa.Status.CONCLUIDO and possui_pendencia and tarefa_liberada:
        status_final = Tarefa.Status.CONCLUIDO_COM_RESTRICAO
    if status == Tarefa.Status.CONCLUIDO and possui_pendencia and not tarefa_liberada:
        if tarefa.tipo == Tarefa.Tipo.FILTRO:
            raise SeparacaoError(FINALIZACAO_FILTRO_PENDENTE_ERRO)
        raise SeparacaoError('Nao e possivel finalizar como CONCLUIDO com itens pendentes')
    if status == Tarefa.Status.CONCLUIDO_COM_RESTRICAO and not tarefa_liberada:
        raise SeparacaoError('Tarefa precisa estar liberada para concluir com restricao')
    if status == Tarefa.Status.FECHADO_COM_RESTRICAO and not (motivo or '').strip():
        raise SeparacaoError('Motivo da restricao e obrigatorio')
    setor_normalizado = _normalizar_setor_operacional(tarefa.setor)
    tarefa_filtros = setor_normalizado == Setor.Codigo.FILTROS
    if tarefa_filtros:
        logger.info(
            'FINALIZANDO FILTROS tarefa_id=%s nf_id=%s user_id=%s status_solicitado=%s',
            tarefa.id,
            tarefa.nf_id,
            getattr(usuario, 'id', None),
            status,
        )
    def _executar():
        with transaction.atomic():
            tarefa_local = _tarefa_lock_queryset().get(id=tarefa_id)
            _validar_nf_cancelada(tarefa_local, usuario, 'SEPARACAO BLOQUEADA')
            _validar_setor_tarefa(tarefa_local, usuario)
            _validar_execucao_tarefa(tarefa_local, usuario)

            tarefa_local.status = status_final
            if tarefa_filtros:
                logger.info(
                    'STATUS FINAL tarefa_id=%s nf_id=%s status=%s',
                    tarefa_local.id,
                    tarefa_local.nf_id,
                    status_final,
                )
            if status_final in {Tarefa.Status.CONCLUIDO, Tarefa.Status.CONCLUIDO_COM_RESTRICAO, Tarefa.Status.FECHADO_COM_RESTRICAO}:
                tarefa_local.usuario = None
                tarefa_local.usuario_em_execucao = None
                tarefa_local.data_inicio = None
                tarefa_local.save(update_fields=['status', 'usuario', 'usuario_em_execucao', 'data_inicio', 'updated_at'])
                from apps.tarefas.services.onda_schema import schema_onda_disponivel

                if schema_onda_disponivel():
                    limpar_referencias_execucao_onda(getattr(tarefa_local, 'onda_id', None))
            else:
                tarefa_local.save(update_fields=['status', 'updated_at'])
            for item_local in TarefaItem.objects.select_for_update(**_select_for_update_kwargs()).filter(tarefa_id=tarefa_id):
                possui_restricao = status_final in {Tarefa.Status.FECHADO_COM_RESTRICAO, Tarefa.Status.CONCLUIDO_COM_RESTRICAO} and item_local.quantidade_separada < item_local.quantidade_total
                if item_local.possui_restricao != possui_restricao:
                    item_local.possui_restricao = possui_restricao
                    item_local.save(update_fields=['possui_restricao', 'updated_at'])
            detalhe = f'Tarefa {tarefa_local.id} finalizada com status {status_final}.'
            if (motivo or '').strip():
                detalhe = f'{detalhe} Motivo: {(motivo or '').strip()}.'
            Log.objects.create(usuario=usuario, acao='FINALIZACAO SEPARACAO', detalhe=detalhe)
            UserActivityLog.objects.create(
                usuario=usuario,
                tipo=UserActivityLog.Tipo.TAREFA_FIM,
                tarefa=tarefa_local,
                timestamp=timezone.now(),
            )
            sincronizar_status_operacional_nfs(_nfs_afetadas_tarefa(tarefa_local))
            if tarefa_filtros and tarefa_local.nf_id and status_final in {Tarefa.Status.CONCLUIDO, Tarefa.Status.CONCLUIDO_COM_RESTRICAO}:
                logger.info(
                    'ENVIANDO PARA CONFERENCIA tarefa_id=%s nf_id=%s status=%s',
                    tarefa_local.id,
                    tarefa_local.nf_id,
                    status_final,
                )
                from apps.conferencia.services.conferencia_service import invalidate_nfs_disponiveis_cache
                from apps.core.services.visibilidade_operacional_service import invalidate_monitoramento_conferencia_cache

                nf_id_filtros = tarefa_local.nf_id
                invalidate_nfs_disponiveis_cache(
                    motivo='finalizacao_separacao_filtros',
                    nf_id=nf_id_filtros,
                    setor=Setor.Codigo.FILTROS,
                )

                def _invalidar_monitoramento_filtros(nf_id=nf_id_filtros):
                    invalidate_monitoramento_conferencia_cache(
                        motivo='finalizacao_separacao_filtros',
                        nf_id=nf_id,
                        setor=Setor.Codigo.FILTROS,
                    )

                transaction.on_commit(_invalidar_monitoramento_filtros)
            return tarefa_local

    tarefa = _executar_com_retry_sqlite_lock(_executar)
    from apps.core.operacional_side_effects import agendar_invalidacao_operacional

    agendar_invalidacao_operacional(motivo='finalizacao_separacao')
    dados = _dados_tarefa(tarefa)
    from apps.core.operacional_transicao import anexar_transicao_separacao

    return anexar_transicao_separacao(dados, usuario, tarefa_id_atual=tarefa.id)


def _validar_nf_cancelada(tarefa, usuario, acao):
    if not tarefa.nf_id:
        return
    from apps.nf.models import NotaFiscal

    nf = getattr(tarefa, 'nf', None)
    if nf is not None and getattr(nf, 'status_fiscal', None):
        status_fiscal = nf.status_fiscal
        numero_nf = nf.numero
    else:
        dados_nf = NotaFiscal.objects.filter(id=tarefa.nf_id).values('status_fiscal', 'numero').first()
        if not dados_nf:
            return
        status_fiscal = dados_nf['status_fiscal']
        numero_nf = dados_nf['numero']
    if status_fiscal == NotaFiscal.StatusFiscal.CANCELADA:
        Log.objects.create(usuario=usuario, acao=acao, detalhe=f'NF {numero_nf} bloqueada. Motivo: NF CANCELADA.')
        raise SeparacaoError(NF_CANCELADA_ERRO)


def _filtrar_tarefas_por_setor(queryset, usuario):
    if usuario is None:
        return queryset
    if _usuario_pode_ver_todos_setores(usuario):
        return queryset
    setores_usuario = _setores_usuario(usuario)
    if not setores_usuario:
        logger.warning(
            'FILTRO_DEBUG user_id=%s setores_usuario=%s filtros_aplicados=%s queryset_final_count=%s',
            getattr(usuario, 'id', None),
            [],
            'tarefas.sem_setor',
            0,
        )
        return queryset.none()
    return queryset.filter(setor__in=setores_usuario)


def _invalidate_dashboards_operacionais(*, motivo=''):
    from apps.core.operacional_side_effects import agendar_invalidacao_operacional

    agendar_invalidacao_operacional(motivo=motivo)


def _validar_setor_tarefa(tarefa, usuario):
    if _usuario_pode_ver_todos_setores(usuario):
        return
    if getattr(usuario, 'perfil', None) == Usuario.Perfil.GESTOR:
        return
    setores_usuario = _setores_usuario(usuario)
    if not setores_usuario:
        raise SeparacaoError(USUARIO_SEM_SETOR_ERRO)
    setor_tarefa = _normalizar_setor_operacional(tarefa.setor)
    if setor_tarefa not in setores_usuario:
        raise SeparacaoError(TAREFA_SETOR_ERRO)


def _validar_execucao_tarefa(tarefa, usuario, exigir_aceite=True):
    if exigir_aceite and tarefa.status == Tarefa.Status.ABERTO:
        raise SeparacaoError(TAREFA_NAO_ACEITA_ERRO)
    usuario_execucao_id = tarefa.usuario_em_execucao_id or tarefa.usuario_id
    if tarefa.status == Tarefa.Status.EM_EXECUCAO and usuario_execucao_id not in {None, usuario.id}:
        raise SeparacaoError(TAREFA_EM_EXECUCAO_ERRO)
    if tarefa.status == Tarefa.Status.EM_EXECUCAO and usuario_execucao_id is None:
        raise SeparacaoError('Tarefa em execução sem responsável. Reabra a tarefa.')


def _validar_produto_no_setor(item, produto, usuario=None, codigo_lido=None):
    item_setor = (item.produto.setor or '').strip().upper()
    produto_setor = (produto.setor or '').strip().upper()
    if produto_setor == item_setor:
        return
    if usuario is not None:
        Log.objects.create(
            usuario=usuario,
            acao='ERRO VALIDACAO PRODUTO SEPARACAO',
            detalhe=(
                f'codigo_lido={codigo_lido}; produto_id={produto.id}; produto_setor={produto_setor}; '
                f'item_id={item.id}; item_setor={item_setor}; usuario={getattr(usuario, "id", None)}; '
                f'timestamp={timezone.now().strftime("%Y-%m-%d %H:%M:%S")}; detalhe=setor_divergente'
            ),
        )
    raise SeparacaoError(
        f'Produto do setor {produto_setor or "-"} não corresponde ao item do setor {item_setor or "-"}'
    )


def _segmento_tarefa(tarefa):
    return tarefa.get_setor_display().upper()


def _tarefa_balcao(tarefa):
    if tarefa.nf_id:
        return bool(getattr(tarefa.nf, 'balcao', False))
    return any(getattr(item.nf, 'balcao', False) for item in tarefa.itens.all() if item.nf_id)


def _nf_tarefa_resumo(tarefa):
    if tarefa.nf_id:
        return tarefa.nf.numero
    numeros = sorted({item.nf.numero for item in tarefa.itens.all() if item.nf_id})
    if not numeros:
        return '-'
    if len(numeros) == 1:
        return numeros[0]
    return ', '.join(numeros)


def _nfs_afetadas_tarefa(tarefa):
    nfs = []
    if tarefa.nf_id:
        nfs.append(tarefa.nf)
    nfs.extend(item.nf for item in tarefa.itens.all() if item.nf_id)
    return nfs


def _dados_tarefa(tarefa):
    return {
        'id': tarefa.id,
        'nf_id': tarefa.nf_id,
        'nf_numero': _nf_tarefa_resumo(tarefa),
        'rota': tarefa.rota.nome,
        'status': tarefa.status,
        'usuario_id': tarefa.usuario_id,
        'usuario_em_execucao_id': tarefa.usuario_em_execucao_id,
        'tipo': tarefa.tipo,
        'setor': tarefa.setor,
        'onda_codigo': tarefa.onda.codigo if tarefa.onda_id and getattr(tarefa, 'onda', None) else '',
        'onda_status': tarefa.onda.status if tarefa.onda_id and getattr(tarefa, 'onda', None) else '',
        'tipo_embalagem': tarefa.tipo_embalagem or '',
        'itens_total': float(tarefa.itens_total or 0),
        'itens_bipados': float(tarefa.itens_bipados or 0),
        'itens_pendentes': float(tarefa.itens_pendentes or 0),
        'percentual': float(tarefa.percentual or 0),
        'segmento': _segmento_tarefa(tarefa),
    }


def _dados_item_operacional_tarefa(item):
    if item is None:
        return None
    return {
        'item_id': item.id,
        'produto': item.produto.cod_prod,
        'descricao': item.produto.descricao,
        'grupo_agregado': item.grupo_agregado.nome if item.grupo_agregado_id else '',
        'setor': item.produto.setor or '',
        'ean': item.produto.cod_ean,
        'nf_numero': item.nf.numero if item.nf_id else None,
        'esperado': float(item.quantidade_total),
        'separado': float(item.quantidade_separada),
    }


def _codigo_exibicao_produto(produto):
    return str(getattr(produto, 'cod_prod', '') or getattr(produto, 'codigo', '') or '').strip()


def sincronizar_conclusao_automatica_tarefa(tarefa, usuario=None):
    possui_pendencia = TarefaItem.objects.filter(tarefa=tarefa, quantidade_separada__lt=F('quantidade_total')).exists()
    if possui_pendencia:
        return False
    if tarefa.status == Tarefa.Status.CONCLUIDO and tarefa.usuario_id is None:
        return False
    tarefa.status = Tarefa.Status.CONCLUIDO
    tarefa.usuario = None
    tarefa.usuario_em_execucao = None
    tarefa.data_inicio = None
    tarefa.save(update_fields=['status', 'usuario', 'usuario_em_execucao', 'data_inicio', 'updated_at'])
    from apps.tarefas.services.onda_schema import schema_onda_disponivel

    if schema_onda_disponivel():
        limpar_referencias_execucao_onda(getattr(tarefa, 'onda_id', None))
    TarefaItem.objects.filter(tarefa=tarefa, possui_restricao=True).update(possui_restricao=False)
    return True


def liberar_execucao_tarefa(tarefa_id, usuario):
    from apps.tarefas.services.onda_schema import queryset_tarefa_web

    tarefa = queryset_tarefa_web().get(id=tarefa_id)
    _validar_setor_tarefa(tarefa, usuario)
    usuario_execucao_id = tarefa.usuario_em_execucao_id or tarefa.usuario_id
    if tarefa.status != Tarefa.Status.EM_EXECUCAO:
        return _dados_tarefa(tarefa)
    if (
        usuario_execucao_id not in {None, usuario.id}
        and not getattr(usuario, 'is_superuser', False)
    ):
        raise SeparacaoError(TAREFA_EM_EXECUCAO_ERRO)
    def _executar():
        with transaction.atomic():
            tarefa_local = Tarefa.objects.select_for_update(**_select_for_update_kwargs()).get(id=tarefa_id)
            _validar_setor_tarefa(tarefa_local, usuario)
            usuario_execucao_local_id = tarefa_local.usuario_em_execucao_id or tarefa_local.usuario_id
            if tarefa_local.status != Tarefa.Status.EM_EXECUCAO:
                return tarefa_local
            if (
                usuario_execucao_local_id not in {None, usuario.id}
                and not getattr(usuario, 'is_superuser', False)
            ):
                raise SeparacaoError(TAREFA_EM_EXECUCAO_ERRO)
            tarefa_local.status = Tarefa.Status.ABERTO
            tarefa_local.usuario = None
            tarefa_local.usuario_em_execucao = None
            tarefa_local.data_inicio = None
            tarefa_local.save(update_fields=['status', 'usuario', 'usuario_em_execucao', 'data_inicio', 'updated_at'])
            from apps.tarefas.services.onda_schema import schema_onda_disponivel

            if schema_onda_disponivel():
                limpar_referencias_execucao_onda(getattr(tarefa_local, 'onda_id', None))
            return tarefa_local

    tarefa = _executar_com_retry_sqlite_lock(_executar)
    return _dados_tarefa(tarefa)


def _is_sqlite_database_locked(exc):
    return connection.vendor == 'sqlite' and 'database is locked' in str(exc).lower()


def _executar_com_retry_sqlite_lock(func):
    for tentativa in range(SQLITE_LOCK_RETRY_MAX):
        try:
            return func()
        except OperationalError as exc:
            if connection.vendor == 'postgresql' and 'could not obtain lock' in str(exc).lower():
                raise SeparacaoError('Tarefa em uso por outra operação. Tente novamente.') from exc
            if not _is_sqlite_database_locked(exc):
                raise
            if tentativa >= SQLITE_LOCK_RETRY_MAX - 1:
                raise SeparacaoError(
                    'Banco ocupado no momento. Aguarde 1 segundo e tente novamente.'
                ) from exc
            time.sleep(SQLITE_LOCK_RETRY_DELAY_BASE_SECONDS * (tentativa + 1))
