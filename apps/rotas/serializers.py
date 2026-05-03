from rest_framework import serializers

from apps.rotas.models import Rota


class RotaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Rota
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')

    def validate(self, attrs):
        data = {}
        if self.instance is not None:
            data = {
                'nome': self.instance.nome,
                'cep_inicial': self.instance.cep_inicial,
                'cep_final': self.instance.cep_final,
                'bairro': self.instance.bairro,
            }
        data.update(attrs)
        for field in ('cep_inicial', 'cep_final', 'bairro'):
            if data.get(field) == '':
                data[field] = None
                attrs[field] = None
        instance = Rota(**data)
        instance.clean()
        return attrs