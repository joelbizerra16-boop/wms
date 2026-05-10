import logging
from datetime import timedelta

from django.utils import timezone

from apps.usuarios.models import UsuarioSessao

HEARTBEAT_SECONDS = 30
SESSION_HEARTBEAT_KEY = '_usuario_sessao_heartbeat_ts'
IGNORED_PREFIXES = ('/static/', '/media/')
IGNORED_PATHS = {'/favicon.ico'}

logger = logging.getLogger(__name__)


def _ignorar_monitoramento(request):
    path = getattr(request, 'path', '') or ''
    if request.method in {'HEAD', 'OPTIONS'}:
        return True
    if path in IGNORED_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in IGNORED_PREFIXES)


def _heartbeat_vencido(request, agora):
    session = getattr(request, 'session', None)
    if session is None:
        return True

    ultimo_heartbeat = session.get(SESSION_HEARTBEAT_KEY)
    if ultimo_heartbeat is not None:
        try:
            if float(ultimo_heartbeat) >= agora.timestamp() - HEARTBEAT_SECONDS:
                return False
        except (TypeError, ValueError):
            pass

    session[SESSION_HEARTBEAT_KEY] = int(agora.timestamp())
    return True


class UsuarioSessaoMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not getattr(request, "user", None) or not request.user.is_authenticated:
            return response
        if _ignorar_monitoramento(request):
            return response

        agora = timezone.now()
        if not _heartbeat_vencido(request, agora):
            return response

        heartbeat_limite = agora - timedelta(seconds=HEARTBEAT_SECONDS)
        try:
            # Evita escrita em toda requisição e usa heartbeat real.
            if not request.user.last_activity or request.user.last_activity < heartbeat_limite:
                request.user.last_activity = agora
                request.user.save(update_fields=["last_activity", "updated_at"])

            sessao = (
                UsuarioSessao.objects
                .filter(usuario_id=request.user.id)
                .only('id', 'ultimo_acesso', 'data_login', 'ativo', 'total_logins_dia')
                .order_by('-ultimo_acesso')
                .first()
            )
            if sessao is None:
                UsuarioSessao.objects.create(usuario=request.user, ativo=True, total_logins_dia=1)
                return response

            UsuarioSessao.objects.filter(usuario_id=request.user.id).exclude(id=sessao.id).delete()

            hoje = timezone.localdate()
            ultimo_acesso_data = timezone.localtime(sessao.ultimo_acesso).date() if sessao.ultimo_acesso else None
            campos_atualizados = ['ultimo_acesso']

            if ultimo_acesso_data != hoje:
                sessao.total_logins_dia = 1
                sessao.data_login = agora
                campos_atualizados.extend(['total_logins_dia', 'data_login'])
            elif sessao.total_logins_dia <= 0:
                sessao.total_logins_dia = 1
                campos_atualizados.append('total_logins_dia')

            if not sessao.ativo:
                sessao.ativo = True
                campos_atualizados.append('ativo')

            sessao.save(update_fields=campos_atualizados)
        except Exception as exc:
            logger.warning('Falha ao atualizar monitoramento de sessao do usuario %s: %s', getattr(request.user, 'id', None), str(exc))
        return response
