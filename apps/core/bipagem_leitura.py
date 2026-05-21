"""Motor enterprise de leitura de código de barras (separação e conferência)."""

from __future__ import annotations

import logging
import re
import time

from django.core.cache import cache

logger = logging.getLogger(__name__)

JANELA_ANTI_DUPLICATA_MS = 120
_CACHE_TTL_DUPLICATA = 1


def sanitizar_entrada_scanner(codigo):
    """Remove lixo de scanner (espaços, CR/LF, TAB) preservando conteúdo."""
    valor = str(codigo or '')
    for caractere in ('\r', '\n', '\t'):
        valor = valor.replace(caractere, '')
    return valor.strip()


def normalizar_codigo_barras(codigo):
    """Extrai dígitos; se > 14 caracteres, usa os 14 últimos (prefixo industrial)."""
    codigo = re.sub(r'\D', '', sanitizar_entrada_scanner(codigo))
    if len(codigo) > 14:
        codigo = codigo[-14:]
    return codigo


def _entrada_e_numerica(valor):
    compacto = re.sub(r'\s+', '', sanitizar_entrada_scanner(valor))
    return bool(compacto) and compacto.isdigit()


def variantes_codigo_barras(codigo):
    """
    Ordem enterprise de tentativa:
    1. código original (alfanumérico ou numérico normalizado)
    2. somente dígitos
    3. sem zeros à esquerda
    4. últimos 14 dígitos
    5. últimos 13 dígitos (EAN-13)
    """
    original = sanitizar_entrada_scanner(codigo)
    if not original:
        return []

    vistos = []
    resultado = []

    def _add(valor):
        if not valor or valor in vistos:
            return
        vistos.append(valor)
        resultado.append(valor)

    if _entrada_e_numerica(original):
        digitos = re.sub(r'\D', '', original)
        _add(digitos)
        _add(normalizar_codigo_barras(digitos))
        sem_zeros = digitos.lstrip('0') or '0'
        _add(sem_zeros)
        if len(digitos) > 14:
            _add(digitos[-14:])
        if len(digitos) >= 13:
            _add(digitos[-13:])
    else:
        _add(''.join(original.split()).upper())

    return resultado


def codigo_bipagem_primario(codigo, *, modulo=None):
    """Código canônico para validação (EAN/GTIN); variantes cobrem matching."""
    original = sanitizar_entrada_scanner(codigo)
    if not original:
        return ''
    if _entrada_e_numerica(original):
        digitos_origem = re.sub(r'\D', '', original)
        primario = normalizar_codigo_barras(digitos_origem)
        if digitos_origem != primario:
            logger.info(
                'CODIGO_NORMALIZADO codigo_original=%s codigo_normalizado=%s modulo=%s',
                digitos_origem,
                primario,
                modulo or 'operacional',
            )
        return primario
    return ''.join(original.split()).upper()


def _chave_anti_duplicata(*, modulo, entidade_id, usuario_id, codigo):
    return f'wms:bip:dup:{modulo}:{entidade_id}:{usuario_id}:{codigo}'


def eh_bipagem_duplicada(*, modulo, entidade_id, usuario_id, codigo):
    codigo = codigo_bipagem_primario(codigo, modulo=modulo)
    if not codigo or not entidade_id or not usuario_id:
        return False
    chave = _chave_anti_duplicata(
        modulo=modulo,
        entidade_id=entidade_id,
        usuario_id=usuario_id,
        codigo=codigo,
    )
    agora = time.time()
    ultimo = cache.get(chave)
    if ultimo and (agora - float(ultimo)) * 1000 < JANELA_ANTI_DUPLICATA_MS:
        logger.info(
            'BIPAGEM_DUPLICADA modulo=%s entidade_id=%s user_id=%s codigo=%s janela_ms=%s',
            modulo,
            entidade_id,
            usuario_id,
            codigo,
            JANELA_ANTI_DUPLICATA_MS,
        )
        return True
    cache.set(chave, agora, _CACHE_TTL_DUPLICATA)
    return False
