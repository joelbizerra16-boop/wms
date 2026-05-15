import time
import traceback
from decimal import Decimal

from django.db import OperationalError, connection, transaction
from django.db.models import F, IntegerField, Max, Q, Sum
from django.db.models.functions import Cast
from django.utils import timezone

from apps.logs.models import Log, UserActivityLog
from apps.core.services.produto_validacao_service import (
    ProdutoValidacaoError,
    selecionar_item_por_codigo_lido,
    validar_produto,
)
from apps.nf.services.status_service import sincronizar_status_operacional_nfs
from apps.produtos.models import Produto
from apps.tarefas.models import Tarefa, TarefaItem
from apps.usuarios.models import Setor
from apps.usuarios.session_utils import usuario_esta_logado


class SeparacaoError(Exception):
    pass


def _tarefa_lock_queryset():
    # PostgreSQL rejects FOR UPDATE on nullable OUTER JOINs, so lock only the
    # tarefa row itself and keep nullable relations lazy-loaded.
    return (
        Tarefa.objects.select_for_update()
        .select_related('rota')
        .prefetch_related('itens__produto', 'itens__nf')
    )


def _itens_pendentes_lock_queryset():
    return TarefaItem.objects.select_for_update(skip_locked=True).select_related('produto')

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
    if usuario is None or not usuario.setores.exists():
        return set()
    setores = list(usuario.setores.values_list('nome', flat=True))
    return {_normalizar_setor_operacional(valor) for valor in setores if _normalizar_setor_operacional(valor)}


def listar_tarefas_disponiveis(usuario=None):
    tarefas = list(
        Tarefa.objects.select_related('nf', 'rota', 'usuario', 'usuario_em_execucao')
        .defer('nf__bairro')
        .prefetch_related('itens__produto', 'itens__nf')
        .filter(ativo=True)
        .filter(status__in=[Tarefa.Status.ABERTO, Tarefa.Status.EM_EXECUCAO])
        .filter(Q(nf__isnull=True) | ~Q(nf__status_fiscal='CANCELADA'))
        .order_by('-id')
    )
    for tarefa in tarefas:
        sincronizar_conclusao_automatica_tarefa(tarefa, usuario)
        if tarefa.status == Tarefa.Status.CONCLUIDO:
            continue
        usuario_responsavel = tarefa.usuario_em_execucao or tarefa.usuario
        if usuario_responsavel and not usuario_esta_logado(usuario_responsavel):
            tarefa.usuario = None
            tarefa.usuario_em_execucao = None
            tarefa.data_inicio = None
            if tarefa.status == Tarefa.Status.EM_EXECUCAO:
                tarefa.status = Tarefa.Status.ABERTO
                tarefa.save(update_fields=['usuario', 'usuario_em_execucao', 'data_inicio', 'status', 'updated_at'])
                continue
            tarefa.save(update_fields=['usuario', 'usuario_em_execucao', 'data_inicio', 'updated_at'])
        if tarefa.status == Tarefa.Status.EM_EXECUCAO and tarefa.usuario_em_execucao_id is None:
            tarefa.status = Tarefa.Status.ABERTO
            tarefa.usuario = None
            tarefa.data_inicio = None
            tarefa.save(update_fields=['status', 'usuario', 'data_inicio', 'updated_at'])

    tarefas = [tarefa for tarefa in tarefas if tarefa.status in STATUS_TAREFA_DISPONIVEL]
    tarefas = _filtrar_tarefas_por_setor(tarefas, usuario)
    tarefas_ordenadas = sorted(
        tarefas,
        key=lambda tarefa: (
            0 if (_tarefa_balcao(tarefa) and tarefa.status == Tarefa.Status.ABERTO) else 1,
            0 if tarefa.status == Tarefa.Status.ABERTO else 1,
            tarefa.id,
        ),
    )
    return [
        {
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
            'bloqueado': bool(
                usuario is not None
                and (tarefa.usuario_em_execucao_id or tarefa.usuario_id)
                and (tarefa.usuario_em_execucao_id or tarefa.usuario_id) != usuario.id
            ),
            'em_uso_por_mim': bool(usuario is not None and (tarefa.usuario_em_execucao_id or tarefa.usuario_id) == usuario.id),
            'balcao': _tarefa_balcao(tarefa),
        }
        for tarefa in tarefas_ordenadas
    ]


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
        print(f'ERRO ITENS SEPARACAO: {exc}')
        traceback.print_exc()

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
        if usuario is None or not usuario.setores.exists():
            raise SeparacaoError(USUARIO_SEM_SETOR_ERRO)

        tarefa = _obter_tarefa_ou_erro(
            Tarefa.objects.select_related('nf', 'rota', 'usuario', 'usuario_em_execucao').defer('nf__bairro').prefetch_related('itens__produto', 'itens__nf'),
            tarefa_id,
        )
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
                identificador = f'NF {tarefa.nf.numero}' if tarefa.nf_id else f'rota {tarefa.rota.nome}'
                _registrar_log_seguro(usuario, 'INICIO SEPARACAO', f'Tarefa {tarefa.id} iniciada para {identificador}.')
                _registrar_atividade_segura(usuario, UserActivityLog.Tipo.TAREFA_INICIO, tarefa, timezone.now())
                return tarefa

        tarefa = _executar_com_retry_sqlite_lock(_executar)
        return _dados_tarefa(tarefa)
    except Exception as exc:
        print(f'ERRO SEPARACAO: {exc}')
        raise


def bipar_tarefa(tarefa_id, codigo, usuario):
    tarefa = (
        Tarefa.objects.select_related('nf', 'rota', 'usuario', 'usuario_em_execucao')
        .defer('nf__bairro')
        .get(id=tarefa_id)
    )
    _validar_nf_cancelada(tarefa, usuario, 'SEPARACAO BLOQUEADA')
    _validar_setor_tarefa(tarefa, usuario)
    _validar_execucao_tarefa(tarefa, usuario)
    if tarefa.status == Tarefa.Status.CONCLUIDO:
        raise SeparacaoError('Tarefa já concluída.')

    def _executar():
        with transaction.atomic():
            tarefa_local = (
                _tarefa_lock_queryset()
                .get(id=tarefa_id)
            )
            _validar_nf_cancelada(tarefa_local, usuario, 'SEPARACAO BLOQUEADA')
            _validar_setor_tarefa(tarefa_local, usuario)
            _validar_execucao_tarefa(tarefa_local, usuario)
            if tarefa_local.status == Tarefa.Status.CONCLUIDO:
                raise SeparacaoError('Tarefa já concluída.')

            # Em ambiente multiusuario, trava e recalcula sempre a partir do banco.
            itens_pendentes_ids = list(
                TarefaItem.objects
                .filter(tarefa=tarefa_local, quantidade_separada__lt=F('quantidade_total'))
                .order_by('nf__data_emissao', 'nf__numero', 'created_at')
                .values_list('id', flat=True)
            )
            itens_pendentes_map = {
                item.id: item
                for item in _itens_pendentes_lock_queryset().filter(id__in=itens_pendentes_ids)
            }
            itens_pendentes = [
                itens_pendentes_map[item_id]
                for item_id in itens_pendentes_ids
                if item_id in itens_pendentes_map
            ]
            if not itens_pendentes:
                raise SeparacaoError('Tarefa sem itens pendentes para bipagem')

            item_esperado = selecionar_item_por_codigo_lido(codigo, itens_pendentes, fallback=itens_pendentes[0])
            try:
                validacao = validar_produto(
                    codigo_lido=codigo,
                    item_id=item_esperado.id,
                    usuario=usuario,
                    item_model=TarefaItem,
                    tipo_validacao='SEPARACAO',
                )
            except ProdutoValidacaoError as exc:
                raise SeparacaoError(str(exc)) from exc

            _validar_produto_no_setor(item=validacao.item, produto=validacao.item.produto, usuario=usuario, codigo_lido=codigo)
            item_local = validacao.item

            if item_local.quantidade_separada >= item_local.quantidade_total:
                raise SeparacaoError('Quantidade excedida')

            item_local.quantidade_separada += Decimal('1')
            item_local.bipado_por = usuario
            item_local.data_bipagem = timezone.now()
            if item_local.quantidade_separada >= item_local.quantidade_total:
                item_local.possui_restricao = False
            item_local.save(update_fields=['quantidade_separada', 'possui_restricao', 'bipado_por', 'data_bipagem', 'updated_at'])
            Log.objects.create(
                usuario=usuario,
                acao='BIPAGEM SEPARACAO',
                detalhe=f'Tarefa {tarefa_local.id} - produto {item_local.produto.cod_prod} bipado.',
            )
            UserActivityLog.objects.create(
                usuario=usuario,
                tipo=UserActivityLog.Tipo.BIPAGEM,
                tarefa=tarefa_local,
                timestamp=timezone.now(),
            )
            if sincronizar_conclusao_automatica_tarefa(tarefa_local, usuario):
                Log.objects.create(
                    usuario=usuario,
                    acao='FINALIZACAO AUTOMATICA SEPARACAO',
                    detalhe=f'Tarefa {tarefa_local.id} finalizada automaticamente apos concluir a bipagem.',
                )
                tarefa_local.refresh_from_db(fields=['status', 'usuario', 'usuario_em_execucao', 'updated_at'])
            sincronizar_status_operacional_nfs(_nfs_afetadas_tarefa(tarefa_local))
            itens_restantes = []
            for item_pendente in itens_pendentes:
                if item_pendente.id == item_local.id:
                    if item_local.quantidade_separada < item_local.quantidade_total:
                        itens_restantes.append(item_local)
                    continue
                itens_restantes.append(item_pendente)
            return tarefa_local, item_local, itens_restantes

    tarefa, item, itens_restantes = _executar_com_retry_sqlite_lock(_executar)
    proximo_item = itens_restantes[0] if itens_restantes else None
    finalizado = proximo_item is None or tarefa.status in {Tarefa.Status.CONCLUIDO, Tarefa.Status.CONCLUIDO_COM_RESTRICAO}
    return {
        'status': 'ok',
        'tarefa_id': tarefa.id,
        'produto': item.produto.cod_prod,
        'nf_numero': item.nf.numero if item.nf_id else tarefa.nf.numero if tarefa.nf_id else None,
        'ean': item.produto.cod_ean,
        'segmento': item.produto.categoria,
        'esperado': float(item.quantidade_total),
        'separado': float(item.quantidade_separada),
        'status_tarefa': tarefa.status,
        'feedback': f'Produto validado no setor {(item.produto.setor or "").strip().upper() or "-"}',
        'cor': 'verde',
        'som': 'beep-curto',
        'proximo_item': _dados_item_operacional_tarefa(proximo_item),
        'finalizado': finalizado,
    }


def finalizar_tarefa(tarefa_id, status, usuario, motivo=None):
    tarefa = (
        Tarefa.objects.select_related('nf', 'usuario', 'usuario_em_execucao')
        .defer('nf__bairro')
        .prefetch_related('itens__nf')
        .get(id=tarefa_id)
    )
    _validar_nf_cancelada(tarefa, usuario, 'SEPARACAO BLOQUEADA')
    _validar_setor_tarefa(tarefa, usuario)
    _validar_execucao_tarefa(tarefa, usuario)
    if status not in {Tarefa.Status.CONCLUIDO, Tarefa.Status.FECHADO_COM_RESTRICAO, Tarefa.Status.CONCLUIDO_COM_RESTRICAO}:
        raise SeparacaoError('Status de finalização inválido')
    possui_pendencia = any(item.quantidade_separada < item.quantidade_total for item in tarefa.itens.all())
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
    def _executar():
        with transaction.atomic():
            tarefa_local = (
                Tarefa.objects.select_for_update()
                .select_related('rota')
                .prefetch_related('itens__nf')
                .get(id=tarefa_id)
            )
            _validar_nf_cancelada(tarefa_local, usuario, 'SEPARACAO BLOQUEADA')
            _validar_setor_tarefa(tarefa_local, usuario)
            _validar_execucao_tarefa(tarefa_local, usuario)

            tarefa_local.status = status_final
            if status_final in {Tarefa.Status.CONCLUIDO, Tarefa.Status.CONCLUIDO_COM_RESTRICAO, Tarefa.Status.FECHADO_COM_RESTRICAO}:
                tarefa_local.usuario = None
                tarefa_local.usuario_em_execucao = None
                tarefa_local.data_inicio = None
                tarefa_local.save(update_fields=['status', 'usuario', 'usuario_em_execucao', 'data_inicio', 'updated_at'])
            else:
                tarefa_local.save(update_fields=['status', 'updated_at'])
            for item_local in tarefa_local.itens.select_for_update().all():
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
            return tarefa_local

    tarefa = _executar_com_retry_sqlite_lock(_executar)
    return _dados_tarefa(tarefa)


def _validar_nf_cancelada(tarefa, usuario, acao):
    if tarefa.nf and tarefa.nf.status_fiscal == 'CANCELADA':
        Log.objects.create(usuario=usuario, acao=acao, detalhe=f'NF {tarefa.nf.numero} bloqueada. Motivo: NF CANCELADA.')
        raise SeparacaoError(NF_CANCELADA_ERRO)


def _filtrar_tarefas_por_setor(queryset, usuario):
    if usuario is None:
        return queryset
    if _usuario_pode_ver_todos_setores(usuario):
        return queryset
    setores_usuario = _setores_usuario(usuario)
    if not setores_usuario:
        return []
    return [
        tarefa
        for tarefa in queryset
        if _normalizar_setor_operacional(tarefa.setor) in setores_usuario
    ]


def _validar_setor_tarefa(tarefa, usuario):
    if _usuario_pode_ver_todos_setores(usuario):
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
    TarefaItem.objects.filter(tarefa=tarefa, possui_restricao=True).update(possui_restricao=False)
    return True


def liberar_execucao_tarefa(tarefa_id, usuario):
    tarefa = Tarefa.objects.select_related('usuario', 'usuario_em_execucao').get(id=tarefa_id)
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
            tarefa_local = Tarefa.objects.select_for_update().select_related('rota').get(id=tarefa_id)
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
            if not _is_sqlite_database_locked(exc):
                raise
            if tentativa >= SQLITE_LOCK_RETRY_MAX - 1:
                raise SeparacaoError(
                    'Banco ocupado no momento. Aguarde 1 segundo e tente novamente.'
                ) from exc
            time.sleep(SQLITE_LOCK_RETRY_DELAY_BASE_SECONDS * (tentativa + 1))