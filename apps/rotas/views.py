from rest_framework import viewsets

from apps.rotas.models import Rota
from apps.rotas.serializers import RotaSerializer


class RotaViewSet(viewsets.ModelViewSet):
    serializer_class = RotaSerializer
    queryset = Rota.objects.all().order_by('nome')
    filterset_fields = ('bairro',)
    search_fields = ('nome', 'bairro', 'cep_inicial', 'cep_final')
    ordering_fields = ('nome', 'created_at', 'updated_at')
