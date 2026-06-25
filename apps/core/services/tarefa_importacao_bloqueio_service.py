from __future__ import annotations

from django.utils import timezone

from apps.conferencia.models import Conferencia
from apps.logs.models import Log
from apps.nf.models import EntradaNF
from apps.tarefas.models import Tarefa

STATUS_TAREFA_BLOQUEIO_IMPORTACAO = (
    Tarefa.Status.ABERTO,
    Tarefa.Status.EM_EXECUCAO,
)


class ImportacaoProdutosBloqueadaError(ValueError):
    """Importação de produtos bloqueada por tarefa(s) operacional(is) ativa(s)."""

    def __init__(self, tarefas):
        self.tarefas = tarefas
        super().__init__(formatar_mensagem_bloqueio_importacao(tarefas))


def queryset_tarefas_bloqueando_importacao():
    return Tarefa.objects.filter(
        ativo=True,
        status__in=STATUS_TAREFA_BLOQUEIO_IMPORTACAO,
    )


def validar_tarefas_antes_importacao_produtos():
    tarefas = listar_tarefas_bloqueando_importacao_detalhadas()
    if tarefas:
        raise ImportacaoProdutosBloqueadaError(tarefas)


def formatar_mensagem_bloqueio_importacao(tarefas):
    if not tarefas:
        return 'Existem tarefas abertas/em execução. Finalize ou pause as tarefas antes de importar uma nova base de produtos.'
    if len(tarefas) == 1:
        tarefa = tarefas[0]
        return (
            f"Importação bloqueada pela Tarefa #{tarefa['id']} criada em {tarefa['criacao_data']} "
            f"({tarefa['dias_parada']} dias sem movimentação)."
        )
    ids = ', '.join(f"#{tarefa['id']}" for tarefa in tarefas)
    return (
        f'Importação bloqueada pelas tarefas {ids}. '
        'Finalize ou pause as tarefas antes de importar uma nova base de produtos.'
    )


def listar_tarefas_bloqueando_importacao_detalhadas():
    queryset = (
        queryset_tarefas_bloqueando_importacao()
        .select_related('nf', 'rota', 'usuario', 'usuario_em_execucao')
        .prefetch_related('itens__produto', 'itens__nf')
        .order_by('created_at')
    )
    return [_serializar_tarefa_bloqueio_importacao(tarefa) for tarefa in queryset]


def _serializar_tarefa_bloqueio_importacao(tarefa):
    diagnostico = montar_diagnostico_operacional_tarefa(tarefa)
    return {
        'id': tarefa.id,
        'tipo': tarefa.tipo,
        'status': tarefa.status,
        'setor': tarefa.setor,
        'criacao': timezone.localtime(tarefa.created_at).isoformat(),
        'criacao_data': timezone.localtime(tarefa.created_at).strftime('%d/%m/%Y'),
        'dias_parada': diagnostico['dias_parada'],
        'nfs': diagnostico['nfs_numeros'],
        'rota': diagnostico['rota_nome'],
        'produtos': diagnostico['produtos'],
        'usuario': diagnostico['usuario_nome'],
        'url_localizar': f'/separacao/{tarefa.id}/',
        'diagnostico': diagnostico,
    }


def montar_diagnostico_operacional_tarefa(tarefa):
    itens = list(tarefa.itens.select_related('produto', 'nf', 'bipado_por').all())
    nfs_map = {}
    for item in itens:
        if item.nf_id and item.nf_id not in nfs_map:
            nfs_map[item.nf_id] = item.nf
    if tarefa.nf_id and tarefa.nf_id not in nfs_map:
        nfs_map[tarefa.nf_id] = tarefa.nf

    nfs_numeros = sorted(
        {nf.numero for nf in nfs_map.values() if nf and nf.numero},
        key=lambda numero: str(numero),
    )
    produtos = sorted(
        {
            (item.produto.cod_prod or item.produto.codigo or str(item.produto_id))
            for item in itens
            if item.produto_id
        }
    )

    ultima_bipagem = None
    for item in itens:
        if item.data_bipagem and (ultima_bipagem is None or item.data_bipagem > ultima_bipagem):
            ultima_bipagem = item.data_bipagem

    ultima_movimentacao = tarefa.updated_at
    if ultima_bipagem and ultima_bipagem > ultima_movimentacao:
        ultima_movimentacao = ultima_bipagem
    if tarefa.data_inicio and tarefa.data_inicio > ultima_movimentacao:
        ultima_movimentacao = tarefa.data_inicio

    agora = timezone.now()
    referencia_parada = ultima_movimentacao or tarefa.created_at
    dias_parada = max((agora - referencia_parada).days, 0)

    quantidade_separada = sum((item.quantidade_separada for item in itens), start=0)
    quantidade_total = sum((item.quantidade_total for item in itens), start=0)
    quantidade_pendente = sum(
        (max(item.quantidade_total - item.quantidade_separada, 0) for item in itens),
        start=0,
    )

    nf_ids = list(nfs_map.keys())
    possui_conferencia = False
    if nf_ids:
        possui_conferencia = Conferencia.objects.filter(nf_id__in=nf_ids).exclude(
            status=Conferencia.Status.CANCELADA
        ).exists()

    usuario_nome = ''
    if tarefa.usuario_em_execucao_id and tarefa.usuario_em_execucao:
        usuario_nome = tarefa.usuario_em_execucao.nome or tarefa.usuario_em_execucao.username
    elif tarefa.usuario_id and tarefa.usuario:
        usuario_nome = tarefa.usuario.nome or tarefa.usuario.username

    auditoria = _montar_auditoria_origem_tarefa(tarefa, nfs_map)

    return {
        'id': tarefa.id,
        'tipo': tarefa.tipo,
        'status': tarefa.status,
        'ativo': tarefa.ativo,
        'setor': tarefa.setor,
        'rota_nome': getattr(tarefa.rota, 'nome', '') if tarefa.rota_id else '',
        'nfs_numeros': nfs_numeros,
        'produtos': produtos,
        'usuario_nome': usuario_nome,
        'dias_parada': dias_parada,
        'ultima_atualizacao': _formatar_data_hora(tarefa.updated_at),
        'ultima_bipagem': _formatar_data_hora(ultima_bipagem),
        'ultima_movimentacao': _formatar_data_hora(ultima_movimentacao),
        'possui_conferencia': possui_conferencia,
        'possui_separacao': bool(itens),
        'quantidade_separada': _formatar_decimal(quantidade_separada),
        'quantidade_pendente': _formatar_decimal(quantidade_pendente),
        'quantidade_total': _formatar_decimal(quantidade_total),
        'criacao': _formatar_data_hora(tarefa.created_at),
        'auditoria': auditoria,
    }


def _montar_auditoria_origem_tarefa(tarefa, nfs_map):
    origem = 'Separação operacional'
    if tarefa.tipo == Tarefa.Tipo.ROTA and not tarefa.nf_id:
        origem = 'Importação XML (tarefa agregada por rota/setor)'
    elif tarefa.tipo == Tarefa.Tipo.FILTRO:
        origem = 'Importação XML (tarefa de filtros por NF)'

    nfs_auditoria = []
    for nf_id, nf in sorted(nfs_map.items(), key=lambda par: par[0]):
        if nf is None:
            continue
        entrada = EntradaNF.objects.filter(chave_nf=nf.chave_nfe).order_by('-data_importacao').first()
        log_importacao = (
            Log.objects.filter(acao='IMPORTACAO XML', detalhe__icontains=str(nf.numero))
            .select_related('usuario')
            .order_by('-created_at')
            .first()
        )
        nfs_auditoria.append(
            {
                'nf_id': nf_id,
                'nf_numero': nf.numero,
                'chave_nfe': nf.chave_nfe,
                'xml_entrada_id': entrada.id if entrada else None,
                'xml_data_importacao': _formatar_data_hora(entrada.data_importacao) if entrada else '',
                'usuario_importacao': (
                    (log_importacao.usuario.nome or log_importacao.usuario.username)
                    if log_importacao and log_importacao.usuario_id
                    else ''
                ),
                'data_importacao': (
                    _formatar_data_hora(log_importacao.created_at) if log_importacao else ''
                ),
            }
        )

    return {
        'origem': origem,
        'nfs': nfs_auditoria,
    }


def _formatar_data_hora(valor):
    if not valor:
        return ''
    return timezone.localtime(valor).strftime('%d/%m/%Y %H:%M')


def _formatar_decimal(valor):
    return f'{valor:.2f}'
