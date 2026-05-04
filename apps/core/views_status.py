from decimal import Decimal
from datetime import date

import logging
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.conferencia.models import Conferencia, ConferenciaItem
from apps.core.services.visibilidade_operacional_service import get_nfs_monitoramento_conferencia
from apps.core.views_dashboard import calcular_indicadores_volume_separacao, collect_itens_filtrados_dashboard_separacao
from apps.nf.models import NotaFiscal
from apps.nf.services.consistencia_service import separacao_concluida_nf
from apps.nf.services.status_service import atualizar_status_nf
from apps.tarefas.models import Tarefa, TarefaItem
from apps.tarefas.services.separacao_service import (
    listar_itens_tarefa_para_exibicao_seguro,
    sincronizar_conclusao_automatica_tarefa,
    status_item_tarefa,
)
from apps.usuarios.access import PerfilPermitido
from apps.usuarios.models import Usuario

logger = logging.getLogger(__name__)


def _safe_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _task_item_status(item):
    return status_item_tarefa(item.tarefa.status, item.quantidade_separada, item.quantidade_total, item.possui_restricao)


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _resolver_periodo_e_busca(request):
    hoje = timezone.localdate()
    date_from_raw = (request.GET.get('date_from') or request.GET.get('data_inicial') or '').strip()
    date_to_raw = (request.GET.get('date_to') or request.GET.get('data_final') or '').strip()
    logger.debug('dashboard_resumo data_inicial=%s', request.GET.get('data_inicial') or request.GET.get('date_from'))
    logger.debug('dashboard_resumo data_final=%s', request.GET.get('data_final') or request.GET.get('date_to'))
    date_from = _parse_date(date_from_raw) or hoje
    date_to = _parse_date(date_to_raw) or hoje
    if date_to < date_from:
        date_to = date_from
    busca = (request.GET.get('busca') or request.GET.get('q') or '').strip().lower()
    return date_from, date_to, busca


def _last_conference(nf):
    return nf.conferencias.exclude(status=Conferencia.Status.CANCELADA).order_by('-created_at').first()


def _nf_status(nf, last_conference):
    if last_conference is None:
        return 'PENDENTE'
    if last_conference.status == Conferencia.Status.EM_CONFERENCIA:
        return 'EM_CONFERENCIA'
    if last_conference.status == Conferencia.Status.AGUARDANDO:
        return 'PENDENTE'
    if last_conference.status == Conferencia.Status.OK:
        return 'CONCLUIDO'
    if last_conference.status == Conferencia.Status.DIVERGENCIA:
        return 'DIVERGENCIA'
    if last_conference.status == Conferencia.Status.LIBERADO_COM_RESTRICAO:
        return 'LIBERADO_COM_RESTRICAO'
    if last_conference.status == Conferencia.Status.CONCLUIDO_COM_RESTRICAO:
        return 'CONCLUIDO_COM_RESTRICAO'
    if nf.status == NotaFiscal.Status.BLOQUEADA_COM_RESTRICAO:
        return 'BLOQUEADA_COM_RESTRICAO'
    if nf.status == NotaFiscal.Status.LIBERADA_COM_RESTRICAO:
        return 'LIBERADA_COM_RESTRICAO'
    if nf.status == NotaFiscal.Status.CONCLUIDO_COM_RESTRICAO:
        return 'CONCLUIDO_COM_RESTRICAO'
    if nf.status == NotaFiscal.Status.CONCLUIDO:
        return 'CONCLUIDO'
    if nf.status == NotaFiscal.Status.EM_CONFERENCIA:
        return 'EM_CONFERENCIA'
    if nf.status == NotaFiscal.Status.INCONSISTENTE:
        return 'INCONSISTENTE'
    return 'PENDENTE'


def _quantidade_separada_nf_item(nf, item_nf):
    tarefa_item_nf = next(
        (
            tarefa_item
            for tarefa in nf.tarefas.all()
            for tarefa_item in tarefa.itens.all()
            if tarefa_item.produto_id == item_nf.produto_id and (not tarefa_item.nf_id or tarefa_item.nf_id == nf.id)
        ),
        None,
    )
    if tarefa_item_nf is not None:
        return min(tarefa_item_nf.quantidade_separada, item_nf.quantidade)

    tarefa_item_rota = (
        TarefaItem.objects.select_related('tarefa')
        .filter(
            tarefa__ativo=True,
            tarefa__nf__isnull=True,
            tarefa__rota=nf.rota,
            nf=nf,
            produto=item_nf.produto,
        )
        .order_by('-updated_at')
        .first()
    )
    if tarefa_item_rota is None:
        return Decimal('0')
    return min(tarefa_item_rota.quantidade_separada, item_nf.quantidade)


def _dashboard_resumo_payload(request):
    date_from, date_to, busca = _resolver_periodo_e_busca(request)
    itens_filtrados = collect_itens_filtrados_dashboard_separacao(request.user, date_from, date_to, busca)
    indicadores_sep = calcular_indicadores_volume_separacao(itens_filtrados)
    total = indicadores_sep['total']
    separado = indicadores_sep['separado']
    pendente = indicadores_sep['pendente']
    em_execucao = indicadores_sep['em_execucao']
    aguardando = indicadores_sep['aguardando']

    nfs_filtradas = get_nfs_monitoramento_conferencia(
        request.user,
        data_inicio=date_from,
        data_fim=date_to,
        busca=busca,
    )
    conferidas = 0
    divergencias = 0
    pendentes = 0
    em_conferencia = 0
    for nf in nfs_filtradas:
        status_nf = str(nf.get('status') or '').upper()
        if status_nf in {'OK', 'CONCLUIDO', 'CONCLUIDO_COM_RESTRICAO'}:
            conferidas += 1
        elif status_nf in {'DIVERGENCIA', 'BLOQUEADA_COM_RESTRICAO'}:
            divergencias += 1
        elif status_nf == 'EM_CONFERENCIA':
            em_conferencia += 1
        else:
            pendentes += 1
    total_nfs = len(nfs_filtradas)
    pendentes = max(total_nfs - conferidas, 0)

    return {
        'total': float(total),
        'separado': float(separado),
        'percentual': float(indicadores_sep['percentual']),
        'pendente': float(pendente),
        'em_execucao': int(em_execucao),
        'aguardando': float(aguardando),
        'total_nfs': total_nfs,
        'conferidas': conferidas,
        'divergencias': divergencias,
        'pendentes': pendentes,
        'em_conferencia': em_conferencia,
    }


class StatusNFAPIView(APIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.CONFERENTE, Usuario.Perfil.GESTOR)

    def get(self, request, nf_id):
        nf = get_object_or_404(
            NotaFiscal.objects.select_related('rota').prefetch_related(
                'itens__produto',
                Prefetch('tarefas', queryset=Tarefa.objects.filter(ativo=True).prefetch_related('itens__produto', 'itens__nf')),
                Prefetch('conferencias', queryset=Conferencia.objects.prefetch_related('itens__produto').order_by('-created_at')),
            ),
            id=nf_id,
            ativa=True,
        )
        if nf.status_fiscal == NotaFiscal.StatusFiscal.CANCELADA:
            return Response({'erro': 'NF cancelada nao disponivel'}, status=status.HTTP_404_NOT_FOUND)

        atualizar_status_nf(nf)
        last_conference = _last_conference(nf)
        conference_items = {item.produto_id: item for item in (last_conference.itens.all() if last_conference else [])}
        itens = []
        for item_nf in nf.itens.all():
            separado = _quantidade_separada_nf_item(nf, item_nf)
            conferencia_item = conference_items.get(item_nf.produto_id)
            conferido = conferencia_item.qtd_conferida if conferencia_item else Decimal('0')
            falta = max(item_nf.quantidade - conferido, Decimal('0'))
            if conferencia_item and conferencia_item.status == ConferenciaItem.Status.DIVERGENCIA:
                status_item = 'DIVERGENCIA'
            elif separado < item_nf.quantidade:
                status_item = 'FALTA SEPARAR'
            elif falta > 0:
                status_item = 'AGUARDANDO'
            else:
                status_item = 'OK'
            itens.append(
                {
                    'produto': item_nf.produto.cod_prod,
                    'descricao': item_nf.produto.descricao,
                    'esperado': float(item_nf.quantidade),
                    'separado': float(separado),
                    'conferido': float(conferido),
                    'falta': float(falta),
                    'status': status_item,
                    'bipado_por': (
                        (conferencia_item.bipado_por.nome or conferencia_item.bipado_por.username)
                        if conferencia_item and conferencia_item.bipado_por_id
                        else ''
                    ),
                    'data_bipagem': conferencia_item.data_bipagem.isoformat() if conferencia_item and conferencia_item.data_bipagem else '',
                }
            )

        if nf.status == NotaFiscal.Status.BLOQUEADA_COM_RESTRICAO:
            status_operacional = 'BLOQUEADA_COM_RESTRICAO'
        elif nf.status == NotaFiscal.Status.LIBERADA_COM_RESTRICAO:
            status_operacional = 'LIBERADA_COM_RESTRICAO'
        else:
            status_operacional = _nf_status(nf, last_conference)

        return Response({'itens': itens, 'nf_status': status_operacional}, status=status.HTTP_200_OK)


class StatusTarefaAPIView(APIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.SEPARADOR, Usuario.Perfil.GESTOR)

    def get(self, request, tarefa_id):
        tarefa = get_object_or_404(
            Tarefa.objects.select_related('nf', 'rota').prefetch_related('itens__produto', 'itens__nf'),
            id=tarefa_id,
            ativo=True,
        )
        print('==== DEBUG STATUS TAREFA ====')
        print(f'ID: {tarefa_id}')
        print(f'USER: {request.user}')
        print(f'TAREFA: {tarefa}')
        print(f'SETOR: {tarefa.setor}')
        print(f'STATUS: {tarefa.status}')

        try:
            setores = list(request.user.setores.values_list('nome', flat=True))
            print(f'SETORES USUARIO: {setores}')
        except Exception as exc:
            print(f'ERRO SETORES: {exc}')
            raise

        itens_rel = getattr(tarefa, 'itens', None)
        if itens_rel:
            try:
                print(f'QTD ITENS REL: {itens_rel.count()}')
            except Exception as exc:
                print(f'ERRO ITENS REL: {exc}')
                raise
        else:
            print('TAREFA SEM ITENS')

        try:
            sincronizar_conclusao_automatica_tarefa(tarefa)
            tarefa.refresh_from_db()
            if tarefa.nf_id and tarefa.nf.status_fiscal == NotaFiscal.StatusFiscal.CANCELADA:
                return Response({'erro': 'Tarefa indisponivel'}, status=status.HTTP_404_NOT_FOUND)

            itens_brutos = listar_itens_tarefa_para_exibicao_seguro(tarefa)
            print(f'ITENS EXIBICAO: {len(itens_brutos)}')
            itens = [
                {
                    'produto': item.get('produto') or '',
                    'descricao': item.get('descricao') or '',
                    'categoria': item.get('categoria') or '',
                    'setor': item.get('setor') or '',
                    'grupo_agregado': item.get('grupo_agregado') or '',
                    'rota': item.get('rota') or '',
                    'nf_numero': item.get('nf_numero'),
                    'agrupado': bool(item.get('agrupado')),
                    'quantidade_total': _safe_float(item.get('quantidade_total')),
                    'quantidade_separada': _safe_float(item.get('quantidade_separada')),
                    'status': item.get('status') or '',
                    'bipado_por': item.get('bipado_por') or '',
                    'data_bipagem': item['data_bipagem'].isoformat() if getattr(item.get('data_bipagem'), 'isoformat', None) else '',
                }
                for item in itens_brutos
            ]
            return Response(
                {
                    'tarefa_id': tarefa.id,
                    'status': tarefa.status,
                    'produto': itens[0]['produto'] if itens else '',
                    'descricao': itens[0]['descricao'] if itens else '',
                    'separado': itens[0]['quantidade_separada'] if itens else 0,
                    'total': itens[0]['quantidade_total'] if itens else 0,
                    'itens': itens,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as exc:
            print(f'ERRO REAL: {exc}')
            logger.exception('Erro real status tarefa: tarefa_id=%s user_id=%s erro=%s', tarefa_id, getattr(request.user, 'id', None), str(exc))
            raise


class DashboardResumoAPIView(APIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.GESTOR,)

    def get(self, request):
        return Response(_dashboard_resumo_payload(request), status=status.HTTP_200_OK)