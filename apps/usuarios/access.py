from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from rest_framework.permissions import BasePermission, IsAuthenticated

from apps.usuarios.models import Usuario


def get_post_login_redirect_url(user):
    if getattr(user, 'perfil', None) == Usuario.Perfil.SEPARADOR:
        return 'web-separacao-lista'
    if getattr(user, 'perfil', None) == Usuario.Perfil.CONFERENTE:
        return 'web-conferencia-lista'
    return 'home'


def build_access_context(user):
    perfil = getattr(user, 'perfil', None)
    is_operacional = perfil in {Usuario.Perfil.SEPARADOR, Usuario.Perfil.CONFERENTE}
    setores = list(user.setores.values_list('nome', flat=True)) if getattr(user, 'is_authenticated', False) else []
    setor_liberado = ', '.join(setores) if setores else getattr(user, 'setor', None)
    return {
        'acesso': {
            'perfil': perfil,
            'is_gestor': perfil == Usuario.Perfil.GESTOR,
            'is_operacional': is_operacional,
            'can_view_home': perfil == Usuario.Perfil.GESTOR,
            'can_manage': perfil == Usuario.Perfil.GESTOR,
            'can_separacao': perfil in {Usuario.Perfil.SEPARADOR, Usuario.Perfil.GESTOR},
            'can_conferencia': perfil in {Usuario.Perfil.CONFERENTE, Usuario.Perfil.GESTOR},
            'setor_liberado': setor_liberado,
        }
    }


def require_profiles(*allowed_profiles):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if getattr(request.user, 'perfil', None) not in allowed_profiles:
                messages.warning(request, 'Acesso redirecionado para a sua area permitida.')
                return redirect(get_post_login_redirect_url(request.user))
            return view_func(request, *args, **kwargs)

        return login_required(_wrapped)

    return decorator


class PerfilPermitido(BasePermission):
    def has_permission(self, request, view):
        if not IsAuthenticated().has_permission(request, view):
            return False
        allowed_profiles = getattr(view, 'allowed_profiles', ())
        if not allowed_profiles:
            return True
        return getattr(request.user, 'perfil', None) in allowed_profiles