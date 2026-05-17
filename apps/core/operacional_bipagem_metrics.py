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
        response_ms = self._fases.get('response', 0.0)
        logger.info(
            'BIPAGEM_TOTAL_MS modulo=%s entidade_id=%s user_id=%s total_ms=%.2f '
            'lock_ms=%.2f query_ms=%.2f save_ms=%.2f response_ms=%.2f %s',
            self.modulo,
            self.entidade_id,
            self.usuario_id,
            total_ms,
            lock_ms,
            query_ms,
            save_ms,
            response_ms,
            extra,
        )
        from django.conf import settings

        slow = float(getattr(settings, 'BIPAGEM_SLOW_LOG_MS', 150))
        if total_ms >= slow:
            logger.warning(
                'BIPAGEM_LENTA modulo=%s entidade_id=%s user_id=%s total_ms=%.2f lock_ms=%.2f query_ms=%.2f',
                self.modulo,
                self.entidade_id,
                self.usuario_id,
                total_ms,
                lock_ms,
                query_ms,
            )
