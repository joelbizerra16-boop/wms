"""Execução em background para tarefas pesadas (não competir com bipagem)."""

import logging
import threading
import uuid

from django.core.cache import cache
from django.db import close_old_connections

logger = logging.getLogger(__name__)

JOB_CACHE_PREFIX = 'wms:async_job'
JOB_CACHE_TTL = 7200


def _job_cache_key(job_id):
    return f'{JOB_CACHE_PREFIX}:{job_id}'


def enqueue_background_job(target, *, label='', user_id=None):
    job_id = uuid.uuid4().hex
    cache.set(
        _job_cache_key(job_id),
        {'status': 'running', 'label': label or '', 'user_id': user_id},
        JOB_CACHE_TTL,
    )

    def _runner():
        close_old_connections()
        try:
            result = target()
            cache.set(
                _job_cache_key(job_id),
                {'status': 'done', 'label': label or '', 'user_id': user_id, 'result': result},
                JOB_CACHE_TTL,
            )
        except Exception as exc:
            logger.exception('ASYNC_JOB_FALHA job_id=%s label=%s', job_id, label)
            cache.set(
                _job_cache_key(job_id),
                {'status': 'error', 'label': label or '', 'user_id': user_id, 'error': str(exc)},
                JOB_CACHE_TTL,
            )
        finally:
            close_old_connections()

    threading.Thread(target=_runner, daemon=True, name=f'wms-async-{label or job_id[:8]}').start()
    return job_id


def get_job_status(job_id):
    if not job_id:
        return None
    return cache.get(_job_cache_key(job_id))
