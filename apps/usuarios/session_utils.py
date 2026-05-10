from datetime import timedelta

from django.contrib.sessions.models import Session
from django.utils import timezone

from apps.usuarios.models import UsuarioSessao


def usuario_esta_logado(usuario):
    if usuario is None:
        return False
    limite_online = timezone.now() - timedelta(minutes=5)
    return UsuarioSessao.objects.filter(
        usuario_id=usuario.id,
        ativo=True,
        ultimo_acesso__gte=limite_online,
    ).only('id').exists()
