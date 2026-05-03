from rest_framework import serializers

from apps.produtos.models import Produto


class ProdutoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Produto
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')