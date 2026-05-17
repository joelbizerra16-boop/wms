"""Cache operacional de curta duração para reduzir queries repetidas na bipagem."""

from django.core.cache import cache

from apps.usuarios.models import Setor

SETORES_USUARIO_TTL = 60


def _normalizar_setor_operacional(valor):
    setor = (valor or '').strip().upper()
    if setor == 'FILTRO':
        return Setor.Codigo.FILTROS
    if setor == 'NAO ENCONTRADO':
        return Setor.Codigo.NAO_ENCONTRADO
    return setor


def setores_usuario_operacional(usuario):
    """Setores normalizados do usuário (cache 60s). None = superuser vê todos."""
    if usuario is None:
        return set()
    if getattr(usuario, 'is_superuser', False):
        return None

    attr = '_wms_setores_operacional'
    if hasattr(usuario, attr):
        return getattr(usuario, attr)

    cache_key = f'wms:op:setores:{usuario.id}'
    cached = cache.get(cache_key)
    if cached is not None:
        valor = set(cached)
        setattr(usuario, attr, valor)
        return valor

    nomes = list(usuario.setores.values_list('nome', flat=True))
    if not nomes and getattr(usuario, 'setor', None) and usuario.setor != Setor.Codigo.NAO_ENCONTRADO:
        nomes = [usuario.setor]
    valor = {_normalizar_setor_operacional(nome) for nome in nomes if _normalizar_setor_operacional(nome)}
    cache.set(cache_key, list(valor), SETORES_USUARIO_TTL)
    setattr(usuario, attr, valor)
    return valor


def usuario_tem_setor_vinculado(usuario):
    from apps.usuarios.models import Usuario

    if usuario is None:
        return False
    if getattr(usuario, 'is_superuser', False):
        return True
    if getattr(usuario, 'perfil', None) == Usuario.Perfil.GESTOR:
        return True
    setores = setores_usuario_operacional(usuario)
    if setores is None:
        return True
    return bool(setores)
