from rest_framework import serializers

from apps.nf.models import NotaFiscal, NotaFiscalItem


class NotaFiscalItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotaFiscalItem
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')


class NotaFiscalSerializer(serializers.ModelSerializer):
    itens = NotaFiscalItemSerializer(many=True, read_only=True)

    class Meta:
        model = NotaFiscal
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')


class XMLImportacaoSerializer(serializers.Serializer):
    file = serializers.FileField(required=True)