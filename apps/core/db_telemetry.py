"""Telemetria PostgreSQL/ORM para caminhos operacionais críticos."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from django.conf import settings
from django.db import connection

logger = logging.getLogger(__name__)

_local = threading.local()
_WRAPPER_INSTALLED = False
_TELEMETRY_CACHE_KEY = 'wms:db:telemetry:snapshot'
_TELEMETRY_CACHE_TTL = 120


@dataclass
class DbScopeStats:
    modulo: str = ''
    operacao: str = ''
    query_count: int = 0
    query_ms: float = 0.0
    slow_query_count: int = 0
    max_query_ms: float = 0.0
    transaction_ms: float = 0.0
    save_count: int = 0
    n_plus_one_suspeito: int = 0
    _ultimo_sql_signature: str = ''
    _repeticoes_sql: int = 0

    def registrar_query(self, sql: str, ms: float):
        self.query_count += 1
        self.query_ms += ms
        if ms > self.max_query_ms:
            self.max_query_ms = ms
        slow_ms = float(getattr(settings, 'DB_QUERY_SLOW_MS', 25))
        if ms >= slow_ms:
            self.slow_query_count += 1
            logger.info(
                'DB_QUERY_MS modulo=%s operacao=%s ms=%.2f sql=%s',
                self.modulo,
                self.operacao,
                ms,
                _sql_resumo(sql),
            )
        assinatura = _sql_resumo(sql)
        if assinatura == self._ultimo_sql_signature:
            self._repeticoes_sql += 1
            if self._repeticoes_sql >= int(getattr(settings, 'ORM_N_PLUS_ONE_THRESHOLD', 8)):
                self.n_plus_one_suspeito += 1
                logger.warning(
                    'ORM_N_PLUS_ONE modulo=%s operacao=%s repeticoes=%s sql=%s',
                    self.modulo,
                    self.operacao,
                    self._repeticoes_sql,
                    assinatura,
                )
        else:
            self._ultimo_sql_signature = assinatura
            self._repeticoes_sql = 1
        if ' FOR UPDATE' in (sql or '').upper():
            logger.info(
                'DB_LOCK_MS modulo=%s operacao=%s ms=%.2f',
                self.modulo,
                self.operacao,
                ms,
            )


def _sql_resumo(sql: str, limite: int = 120) -> str:
    compacto = ' '.join((sql or '').split())
    if len(compacto) <= limite:
        return compacto
    return compacto[:limite] + '...'


def _stats_atual():
    stats = getattr(_local, 'stats', None)
    if stats is None:
        stats = DbScopeStats()
        _local.stats = stats
    return stats


def _execute_wrapper(execute, sql, params, many, context):
    inicio = time.perf_counter()
    try:
        return execute(sql, params, many, context)
    finally:
        stats = getattr(_local, 'stats', None)
        if stats is None:
            return
        ms = (time.perf_counter() - inicio) * 1000
        stats.registrar_query(sql or '', ms)


def install_db_execute_wrapper():
    global _WRAPPER_INSTALLED
    if _WRAPPER_INSTALLED:
        return
    if not getattr(settings, 'DB_TELEMETRY_ENABLED', True):
        return
    connection.execute_wrappers.insert(0, _execute_wrapper)
    _WRAPPER_INSTALLED = True


@contextmanager
def operacional_db_scope(modulo: str, operacao: str):
    """Contexto de telemetria por operação (bipagem, listagem, etc.)."""
    install_db_execute_wrapper()
    anterior = getattr(_local, 'stats', None)
    stats = DbScopeStats(modulo=modulo, operacao=operacao)
    _local.stats = stats
    inicio_tx = time.perf_counter()
    try:
        yield stats
    finally:
        stats.transaction_ms = (time.perf_counter() - inicio_tx) * 1000
        logger.info(
            'DB_TRANSACTION_MS modulo=%s operacao=%s total_ms=%.2f query_count=%s query_ms=%.2f '
            'slow_queries=%s max_query_ms=%.2f n_plus_one_suspeito=%s',
            stats.modulo,
            stats.operacao,
            stats.transaction_ms,
            stats.query_count,
            stats.query_ms,
            stats.slow_query_count,
            stats.max_query_ms,
            stats.n_plus_one_suspeito,
        )
        _publicar_snapshot(stats)
        _local.stats = anterior


def registrar_db_deadlock(modulo: str, operacao: str, detalhe: str = ''):
    logger.error(
        'DB_DEADLOCK modulo=%s operacao=%s %s',
        modulo,
        operacao,
        detalhe,
    )


def _publicar_snapshot(stats: DbScopeStats):
    try:
        from django.core.cache import cache

        atual = cache.get(_TELEMETRY_CACHE_KEY) or {}
        chave = f'{stats.modulo}:{stats.operacao}'
        atual[chave] = {
            'modulo': stats.modulo,
            'operacao': stats.operacao,
            'transaction_ms': round(stats.transaction_ms, 2),
            'query_count': stats.query_count,
            'query_ms': round(stats.query_ms, 2),
            'slow_query_count': stats.slow_query_count,
            'max_query_ms': round(stats.max_query_ms, 2),
            'n_plus_one_suspeito': stats.n_plus_one_suspeito,
            'ts': time.time(),
        }
        cache.set(_TELEMETRY_CACHE_KEY, atual, _TELEMETRY_CACHE_TTL)
    except Exception:
        pass


def obter_stats_escopo_atual():
    return getattr(_local, 'stats', None)


def obter_snapshot_telemetria():
    try:
        from django.core.cache import cache

        return cache.get(_TELEMETRY_CACHE_KEY) or {}
    except Exception:
        return {}


def registrar_cache_hit(modulo: str, chave: str):
    logger.info('CACHE_HIT modulo=%s chave=%s', modulo, chave)


def registrar_cache_miss(modulo: str, chave: str):
    logger.info('CACHE_MISS modulo=%s chave=%s', modulo, chave)
