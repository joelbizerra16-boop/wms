from rest_framework import viewsets

from apps.clientes.models import Cliente
from apps.clientes.serializers import ClienteSerializer


class ClienteViewSet(viewsets.ModelViewSet):
    serializer_class = ClienteSerializer
    queryset = Cliente.objects.all().order_by('nome')
    filterset_fields = ('inscricao_estadual',)
    search_fields = ('nome', 'inscricao_estadual')
    ordering_fields = ('nome', 'created_at', 'updated_at')
