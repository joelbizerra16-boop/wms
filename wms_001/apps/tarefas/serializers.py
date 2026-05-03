from rest_framework import serializers

from apps.tarefas.models import Tarefa, TarefaItem


class TarefaItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = TarefaItem
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')


class TarefaSerializer(serializers.ModelSerializer):
    itens = TarefaItemSerializer(many=True, read_only=True)

    class Meta:
        model = Tarefa
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')