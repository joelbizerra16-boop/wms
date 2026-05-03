from rest_framework import viewsets

from apps.produtos.models import Produto
from apps.produtos.serializers import ProdutoSerializer


class ProdutoViewSet(viewsets.ModelViewSet):
    serializer_class = ProdutoSerializer
    queryset = Produto.objects.all().order_by('cod_prod')
    filterset_fields = ('categoria', 'cod_prod', 'cod_ean')
    search_fields = ('cod_prod', 'descricao', 'cod_ean')
    ordering_fields = ('cod_prod', 'descricao', 'created_at', 'updated_at')
