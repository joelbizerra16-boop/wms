"""Métricas leves do caminho crítico de bipagem (sem profiling global de queries)."""

import logging
import time
from contextlib import contextmanager

from django.conf import settings

logger = logging.getLogger(__name__)


class BipagemMetrics:
    """Mede lock/query/save/response com perf_counter; não altera fluxo operacional."""

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
            ms = (time.perf_counter() - inicio) * 1000
            self._fases[nome] = self._fases.get(nome, 0.0) + ms

    def _ms(self, nome):
        return self._fases.get(nome, 0.0)

    def registrar(self):
        if not getattr(settings, 'BIPAGEM_METRICS_ENABLED', True):
            return

        total_ms = (time.perf_counter() - self._inicio_total) * 1000
        lock_ms = self._ms('lock')
        query_ms = self._ms('query')
        save_ms = self._ms('save')
        response_ms = self._ms('response')

        extras = []
        if self.cache_hit is True:
            extras.append('CACHE_HIT=1')
        elif self.cache_hit is False:
            extras.append('CACHE_MISS=1')
        if self.duplicada:
            extras.append('BIPAGEM_DUPLICADA=1')
        extra_txt = ' '.join(extras)

        logger.info(
            'BIPAGEM_TOTAL_MS modulo=%s entidade_id=%s user_id=%s total_ms=%.2f '
            'query_ms=%.2f lock_ms=%.2f save_ms=%.2f response_ms=%.2f %s',
            self.modulo,
            self.entidade_id,
            self.usuario_id,
            total_ms,
            query_ms,
            lock_ms,
            save_ms,
            response_ms,
            extra_txt,
        )

        slow = float(getattr(settings, 'BIPAGEM_SLOW_LOG_MS', 150))
        if total_ms >= slow:
            logger.warning(
                'BIPAGEM_LENTA modulo=%s entidade_id=%s user_id=%s total_ms=%.2f '
                'query_ms=%.2f lock_ms=%.2f save_ms=%.2f response_ms=%.2f',
                self.modulo,
                self.entidade_id,
                self.usuario_id,
                total_ms,
                query_ms,
                lock_ms,
                save_ms,
                response_ms,
            )
