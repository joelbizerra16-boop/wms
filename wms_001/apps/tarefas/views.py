from rest_framework import viewsets

from apps.tarefas.models import Tarefa, TarefaItem
from apps.tarefas.serializers import TarefaItemSerializer, TarefaSerializer


class TarefaViewSet(viewsets.ModelViewSet):
    serializer_class = TarefaSerializer
    filterset_fields = ('tipo', 'status', 'rota', 'nf')
    search_fields = ('id', 'nf__numero', 'rota__nome')
    ordering_fields = ('created_at', 'updated_at', 'status')

    def get_queryset(self):
        return (
            Tarefa.objects.select_related('nf', 'rota')
            .prefetch_related('itens__produto')
            .exclude(nf__status_fiscal='CANCELADA')
            .order_by('-created_at')
        )


class TarefaItemViewSet(viewsets.ModelViewSet):
    serializer_class = TarefaItemSerializer
    filterset_fields = ('tarefa', 'produto')
    search_fields = ('tarefa__id', 'produto__cod_prod', 'produto__descricao')
    ordering_fields = ('created_at', 'updated_at', 'quantidade_total', 'quantidade_separada')

    def get_queryset(self):
        return (
            TarefaItem.objects.select_related('tarefa', 'produto', 'tarefa__rota', 'tarefa__nf')
            .exclude(tarefa__nf__status_fiscal='CANCELADA')
            .order_by('tarefa_id', 'produto_id')
        )
