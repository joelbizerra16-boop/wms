from rest_framework import serializers

from apps.usuarios.models import Usuario


class UsuarioSerializer(serializers.ModelSerializer):
    senha = serializers.CharField(write_only=True, required=False, allow_blank=False)
    setores = serializers.ListField(child=serializers.CharField(), required=False)

    class Meta:
        model = Usuario
        fields = (
            'id',
            'nome',
            'username',
            'senha',
            'perfil',
            'setor',
            'setores',
            'is_active',
            'is_staff',
            'created_at',
            'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')

    def create(self, validated_data):
        senha = validated_data.pop('senha', None)
        setores = validated_data.pop('setores', None)
        user = Usuario(**validated_data)
        if senha:
            user.set_password(senha)
        else:
            user.set_unusable_password()
        user.save()
        if setores is not None:
            user.definir_setores(setores)
        return user

    def update(self, instance, validated_data):
        senha = validated_data.pop('senha', None)
        setores = validated_data.pop('setores', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if senha:
            instance.set_password(senha)
        instance.save()
        if setores is not None:
            instance.definir_setores(setores)
        return instance

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['setores'] = list(instance.setores.values_list('nome', flat=True))
        if not data['setores'] and data.get('setor'):
            data['setores'] = [data['setor']]
        return data