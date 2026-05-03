from datetime import timedelta

from django.utils import timezone

from apps.usuarios.models import UsuarioSessao

HEARTBEAT_SECONDS = 30


class UsuarioSessaoMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not getattr(request, "user", None) or not request.user.is_authenticated:
            return response

        agora = timezone.now()
        heartbeat_limite = agora - timedelta(seconds=HEARTBEAT_SECONDS)

        # Evita escrita em toda requisição e usa heartbeat real.
        if not request.user.last_activity or request.user.last_activity < heartbeat_limite:
            request.user.last_activity = agora
            request.user.save(update_fields=["last_activity", "updated_at"])

        sessoes = list(UsuarioSessao.objects.filter(usuario=request.user).order_by("-ultimo_acesso"))
        if not sessoes:
            sessao = UsuarioSessao.objects.create(usuario=request.user, ativo=True, total_logins_dia=1)
            created = True
        else:
            sessao = sessoes[0]
            created = False
            if len(sessoes) > 1:
                UsuarioSessao.objects.filter(id__in=[s.id for s in sessoes[1:]]).delete()

        hoje = timezone.localdate()
        ultimo_acesso_data = timezone.localtime(sessao.ultimo_acesso).date() if sessao.ultimo_acesso else None

        if not created and ultimo_acesso_data != hoje:
            sessao.total_logins_dia = 1
            sessao.data_login = agora
        elif not created and sessao.total_logins_dia <= 0:
            sessao.total_logins_dia = 1

        sessao.ativo = True
        sessao.save(update_fields=["total_logins_dia", "data_login", "ativo", "ultimo_acesso"])
        return response
