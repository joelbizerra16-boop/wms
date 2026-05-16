"""Armazenamento leve de IDs de scan de ativação (evita inflar sessão Django)."""

from django.core.cache import cache

SCAN_CACHE_PREFIX = 'wms:scan_entradas_nf_ids'
SCAN_CACHE_TTL = 3600


def _scan_cache_key(user_id):
    return f'{SCAN_CACHE_PREFIX}:{user_id}'


def get_scan_entrada_ids(user_id):
    ids = cache.get(_scan_cache_key(user_id), [])
    if not isinstance(ids, list):
        return []
    return [int(i) for i in ids if str(i).isdigit()]


def set_scan_entrada_ids(user_id, ids):
    normalizados = [int(i) for i in ids if str(i).isdigit()]
    cache.set(_scan_cache_key(user_id), normalizados, SCAN_CACHE_TTL)


def clear_scan_entrada_ids(user_id):
    cache.delete(_scan_cache_key(user_id))
