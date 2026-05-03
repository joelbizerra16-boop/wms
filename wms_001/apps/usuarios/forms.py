from django import forms

from apps.usuarios.models import Setor, Usuario


class UsuarioForm(forms.ModelForm):
    senha = forms.CharField(widget=forms.PasswordInput, required=True)
    setores = forms.ModelMultipleChoiceField(
        queryset=Setor.objects.order_by('nome'),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        error_messages={'required': 'Selecione pelo menos um setor.'},
    )

    class Meta:
        model = Usuario
        fields = ['nome', 'username', 'senha', 'perfil', 'setores', 'is_active', 'is_staff']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        Setor.garantir_setores_padrao()
        self.fields['setores'].queryset = Setor.objects.order_by('nome')

    def save(self, commit=True):
        usuario = super().save(commit=False)
        senha = self.cleaned_data.get('senha')
        if senha:
            usuario.set_password(senha)
        else:
            usuario.set_unusable_password()

        setores = list(self.cleaned_data.get('setores') or [])
        usuario.setor = setores[0].nome if setores else Setor.Codigo.NAO_ENCONTRADO

        if commit:
            usuario.save()
            usuario.definir_setores([setor.nome for setor in setores])
        return usuario

    def clean_setores(self):
        setores = self.cleaned_data.get('setores')
        if not setores:
            raise forms.ValidationError('Selecione pelo menos um setor.')
        return setores
