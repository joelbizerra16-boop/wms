from rest_framework import viewsets

from apps.logs.models import Log
from apps.logs.serializers import LogSerializer


class LogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = LogSerializer
    filterset_fields = ('usuario', 'acao')
    search_fields = ('usuario__nome', 'usuario__username', 'acao', 'detalhe')
    ordering_fields = ('created_at', 'updated_at', 'acao')

    def get_queryset(self):
        return Log.objects.select_related('usuario').order_by('-created_at')
