from rest_framework import serializers

from apps.conferencia.models import Conferencia, ConferenciaItem


class ConferenciaItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConferenciaItem
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')


class ConferenciaSerializer(serializers.ModelSerializer):
    itens = ConferenciaItemSerializer(many=True, read_only=True)

    class Meta:
        model = Conferencia
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')


class IniciarConferenciaSerializer(serializers.Serializer):
    nf_id = serializers.IntegerField(min_value=1)


class BiparConferenciaSerializer(serializers.Serializer):
    conferencia_id = serializers.IntegerField(min_value=1)
    codigo = serializers.CharField(max_length=50)


class RegistrarDivergenciaSerializer(serializers.Serializer):
    item_id = serializers.IntegerField(min_value=1)
    motivo = serializers.ChoiceField(choices=ConferenciaItem.MotivoDivergencia.choices)
    observacao = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class FinalizarConferenciaSerializer(serializers.Serializer):
    conferencia_id = serializers.IntegerField(min_value=1)