from django.contrib.sessions.models import Session
from django.utils import timezone


def usuario_esta_logado(usuario):
    if usuario is None:
        return False
    sessoes = Session.objects.filter(expire_date__gte=timezone.now())
    usuario_id = str(usuario.id)
    for sessao in sessoes:
        dados = sessao.get_decoded()
        if dados.get('_auth_user_id') == usuario_id:
            return True
    return False
