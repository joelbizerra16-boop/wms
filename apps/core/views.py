from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
import logging
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.conferencia.models import Conferencia, ConferenciaItem
from apps.conferencia.services.conferencia_service import listar_nfs_disponiveis
from apps.nf.models import NotaFiscal
from apps.tarefas.models import Tarefa, TarefaItem
from apps.usuarios.access import build_access_context, require_profiles
from apps.usuarios.models import Setor, Usuario

logger = logging.getLogger(__name__)


class HealthCheckView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({'status': 'ok'}, status=status.HTTP_200_OK)


@require_profiles(Usuario.Perfil.GESTOR)
def home(request):
    context = {'usuario': request.user}
    context.update(build_access_context(request.user))
    return render(request, 'home.html', context)


@require_profiles(Usuario.Perfil.GESTOR)
def dashboard_data(request):
    try:
        hoje = timezone.localdate()
        use_cache = not settings.DEBUG
        cache_key = f'dashboard_data:{request.user.id}:{hoje.isoformat()}'
        if use_cache:
            cached_payload = cache.get(cache_key)
            if cached_payload is not None:
                return JsonResponse(cached_payload)
        tarefas_base = (
            Tarefa.objects.select_related('nf')
            .filter(ativo=True)
            .filter(created_at__date=hoje)
            .filter(Q(nf__isnull=True) | ~Q(nf__status_fiscal=NotaFiscal.StatusFiscal.CANCELADA))
        )
        _sincronizar_status_tarefas_por_quantidade(tarefas_base)
        tarefas_base = tarefas_base.select_related('nf')

        itens = list(
            TarefaItem.objects.select_related('tarefa', 'nf')
            .filter(tarefa__in=tarefas_base)
        )

        setor_acumulado = {
            Setor.Codigo.LUBRIFICANTE: {'separado': 0.0, 'pendente': 0.0},
            Setor.Codigo.FILTROS: {'separado': 0.0, 'pendente': 0.0},
            Setor.Codigo.AGREGADO: {'separado': 0.0, 'pendente': 0.0},
            Setor.Codigo.NAO_ENCONTRADO: {'separado': 0.0, 'pendente': 0.0},
        }
        for item in itens:
            setor = item.tarefa.setor if item.tarefa.setor in setor_acumulado else Setor.Codigo.NAO_ENCONTRADO
            total = float(item.quantidade_total or 0)
            separado = min(float(item.quantidade_separada or 0), total)
            pendente = max(total - separado, 0)
            setor_acumulado[setor]['separado'] += separado
            setor_acumulado[setor]['pendente'] += pendente

        setores_payload = [
            {
                'setor': 'Lubrificante',
                'separado': round(setor_acumulado[Setor.Codigo.LUBRIFICANTE]['separado'], 2),
                'pendente': round(setor_acumulado[Setor.Codigo.LUBRIFICANTE]['pendente'], 2),
            },
            {
                'setor': 'Filtro',
                'separado': round(setor_acumulado[Setor.Codigo.FILTROS]['separado'], 2),
                'pendente': round(setor_acumulado[Setor.Codigo.FILTROS]['pendente'], 2),
            },
            {
                'setor': 'Agregado',
                'separado': round(setor_acumulado[Setor.Codigo.AGREGADO]['separado'], 2),
                'pendente': round(setor_acumulado[Setor.Codigo.AGREGADO]['pendente'], 2),
            },
            {
                'setor': 'Não encontrado',
                'separado': round(setor_acumulado[Setor.Codigo.NAO_ENCONTRADO]['separado'], 2),
                'pendente': round(setor_acumulado[Setor.Codigo.NAO_ENCONTRADO]['pendente'], 2),
            },
        ]

        nfs_hoje_qs = (
            NotaFiscal.objects.select_related('cliente')
            .filter(ativa=True)
            .filter(created_at__date=hoje)
            .exclude(status_fiscal=NotaFiscal.StatusFiscal.CANCELADA)
        )
        nfs_operacao = nfs_hoje_qs.order_by('-updated_at')[:12]
        prioridade_ordem = {'PENDENTE': 'ALTA', 'EM_CONFERENCIA': 'ALTA'}
        status_legivel = {
            'PENDENTE': 'SEPARAÇÃO',
            'EM_CONFERENCIA': 'CONFERÊNCIA',
            'CONCLUIDO': 'FINALIZADA',
            'CONCLUIDO_COM_RESTRICAO': 'FINALIZADA',
        }
        lista_status = [
            {
                'nf': nf.numero,
                'cliente': nf.cliente.nome if nf.cliente_id else 'CONSOLIDADO',
                'status': status_legivel.get(nf.status, nf.status.replace('_', ' ')),
                'prioridade': prioridade_ordem.get(nf.status, 'BAIXA'),
            }
            for nf in nfs_operacao
        ]
        tarefas_em_separacao = tarefas_base.filter(
            status__in=[Tarefa.Status.ABERTO, Tarefa.Status.EM_EXECUCAO]
        )
        # No fluxo operacional, a NF pode estar associada ao item da tarefa e nao
        # diretamente em tarefa.nf; por isso o Home precisa consolidar ambos.
        nf_ids_monitoradas = {
            tarefa.nf_id for tarefa in tarefas_base if tarefa.nf_id
        }
        nf_ids_monitoradas.update(item.nf_id for item in itens if item.nf_id)

        nfs_com_pendencia_conferencia = [
            nf
            for nf in listar_nfs_disponiveis(request.user)
            if nf.get('id') in nf_ids_monitoradas
            and (
                nf.get('status_separacao') == 'SEPARADO'
                or int(nf.get('itens_pendentes_conferencia') or 0) > 0
            )
        ]

        total_conferencias = Conferencia.objects.count()
        conferencias_com_pendencia = sum(
            1
            for conferencia in Conferencia.objects.exclude(status=Conferencia.Status.CANCELADA).prefetch_related('itens')
            if any(
                item.status in {ConferenciaItem.Status.AGUARDANDO, ConferenciaItem.Status.DIVERGENCIA}
                for item in conferencia.itens.all()
            )
        )
        print('TOTAL CONFERENCIAS:', total_conferencias)
        print('COM PENDENCIA:', conferencias_com_pendencia)
        logger.info(
            'dashboard_data conferencia total_conferencias=%s com_pendencia=%s nfs_monitoradas=%s nfs_com_pendencia=%s',
            total_conferencias,
            conferencias_com_pendencia,
            len(nf_ids_monitoradas),
            len(nfs_com_pendencia_conferencia),
        )

        total_separacao = tarefas_em_separacao.count()
        total_conferencia = len(nfs_com_pendencia_conferencia)
        total_tarefas = tarefas_base.count()
        total_finalizadas = total_tarefas - (total_separacao + total_conferencia)
        if total_finalizadas < 0:
            total_finalizadas = 0
        total_operacao = total_separacao + total_conferencia + total_finalizadas
        percentual_separado = round((total_finalizadas / total_operacao * 100) if total_operacao > 0 else 0, 2)
        percentual_conferir = round(100 - percentual_separado, 2) if total_operacao > 0 else 0

        for tarefa in tarefas_base:
            etapa = 'EM_SEPARACAO' if tarefa.status == Tarefa.Status.EM_EXECUCAO else (
                'EM_CONFERENCIA' if tarefa.status in {Tarefa.Status.CONCLUIDO, Tarefa.Status.CONCLUIDO_COM_RESTRICAO} else 'OUTRA'
            )
            logger.debug(
                'dashboard_data tarefa_id=%s status=%s etapa=%s',
                tarefa.id,
                tarefa.status,
                etapa,
            )

        nfs_prioridade_alta = (total_separacao + total_conferencia) if (total_separacao or total_conferencia) else 0

        logger.info(
            'dashboard_data resumo data=%s total=%s em_separacao=%s em_conferencia=%s finalizadas=%s alerta_alta=%s',
            hoje,
            total_tarefas,
            total_separacao,
            total_conferencia,
            total_finalizadas,
            nfs_prioridade_alta,
        )

        payload = {
            'total_nfs': total_tarefas,
            'em_separacao': total_separacao,
            'em_conferencia': total_conferencia,
            'finalizadas': total_finalizadas,
            'percentual_separado': percentual_separado,
            'percentual_conferir': percentual_conferir,
            'dados_setor': setores_payload,
            'status_operacional': lista_status,
            'alertas_alta': nfs_prioridade_alta,
            'data_referencia': hoje.strftime('%d/%m/%Y'),
        }
        if use_cache:
            cache.set(cache_key, payload, timeout=8)
        return JsonResponse(payload)
    except Exception as exc:
        logger.exception('Erro no endpoint /api/dashboard/')
        return JsonResponse({'erro': str(exc)}, status=500)


def _sincronizar_status_tarefas_por_quantidade(tarefas_queryset):
    for tarefa in tarefas_queryset.prefetch_related('itens'):
        itens = list(tarefa.itens.all())
        if not itens:
            continue
        todos_separados = all(item.quantidade_separada >= item.quantidade_total for item in itens)
        possui_movimentacao = any(item.quantidade_separada > 0 for item in itens)
        novo_status = tarefa.status
        if todos_separados:
            novo_status = Tarefa.Status.CONCLUIDO
        elif possui_movimentacao and tarefa.status == Tarefa.Status.ABERTO:
            novo_status = Tarefa.Status.EM_EXECUCAO
        if novo_status != tarefa.status:
            tarefa.status = novo_status
            tarefa.save(update_fields=['status', 'updated_at'])
