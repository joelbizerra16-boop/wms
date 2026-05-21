"""Métricas estruturadas do caminho crítico de bipagem."""

import logging
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class BipagemMetrics:
    def __init__(self, modulo, entidade_id, usuario_id):
        self.modulo = modulo
        self.entidade_id = entidade_id
        self.usuario_id = usuario_id
        self._inicio_total = time.perf_counter()
        self._fases = {}
        self.cache_hit = None
        self.duplicada = False

    @contextmanager
    def fase(self, nome):
        inicio = time.perf_counter()
        try:
            yield
        finally:
            self._fases[nome] = (time.perf_counter() - inicio) * 1000

    def registrar(self, *, extra=''):
        total_ms = (time.perf_counter() - self._inicio_total) * 1000
        lock_ms = self._fases.get('lock', 0.0)
        query_ms = self._fases.get('query', 0.0)
        save_ms = self._fases.get('save', 0.0)
        serialize_ms = self._fases.get('serialize', 0.0)
        batch_ms = self._fases.get('batch', 0.0)
        side_effects_ms = self._fases.get('side_effects', 0.0)
        cache_ms = self._fases.get('cache', 0.0)
        response_ms = self._fases.get('response', 0.0)
        cache_flag = ''
        if self.cache_hit is True:
            cache_flag = 'CACHE_HIT=1'
        elif self.cache_hit is False:
            cache_flag = 'CACHE_MISS=1'
        if self.duplicada:
            cache_flag = f'{cache_flag} BIPAGEM_DUPLICADA=1'.strip()
        logger.info(
            'BIPAGEM_TOTAL_MS modulo=%s entidade_id=%s user_id=%s total_ms=%.2f '
            'LOCK_MS=%.2f QUERY_MS=%.2f save_ms=%.2f serialize_ms=%.2f batch_ms=%.2f '
            'ASYNC_SIDE_EFFECT=%.2f cache_ms=%.2f response_ms=%.2f %s %s',
            self.modulo,
            self.entidade_id,
            self.usuario_id,
            total_ms,
            lock_ms,
            query_ms,
            save_ms,
            serialize_ms,
            batch_ms,
            side_effects_ms,
            cache_ms,
            response_ms,
            cache_flag,
            extra,
        )
        from django.conf import settings

        slow = float(getattr(settings, 'BIPAGEM_SLOW_LOG_MS', 150))
        if total_ms >= slow:
            logger.warning(
                'BIPAGEM_LENTA modulo=%s entidade_id=%s user_id=%s total_ms=%.2f lock_ms=%.2f query_ms=%.2f save_ms=%.2f serialize_ms=%.2f batch_ms=%.2f side_effects_ms=%.2f cache_ms=%.2f',
                self.modulo,
                self.entidade_id,
                self.usuario_id,
                total_ms,
                lock_ms,
                query_ms,
                save_ms,
                serialize_ms,
                batch_ms,
                side_effects_ms,
                cache_ms,
            )
