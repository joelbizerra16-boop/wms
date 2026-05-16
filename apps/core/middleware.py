import logging
import time

from django.conf import settings


logger = logging.getLogger(__name__)


class RequestTimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started_at = time.perf_counter()
        response = self.get_response(request)
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        path = getattr(request, 'path', '') or ''
        perf_paths = ('/api/separacao/', '/api/conferencia/', '/dashboard/', '/separacao/', '/conferencia/')
        slow_threshold = float(getattr(settings, 'REQUEST_SLOW_LOG_MS', 300))
        critical_threshold = float(getattr(settings, 'REQUEST_CRITICAL_LOG_MS', 800))

        if path.startswith(perf_paths) and elapsed_ms >= slow_threshold:
            log_method = logger.warning if elapsed_ms >= critical_threshold else logger.info
            log_method(
                'REQUEST_MS metodo=%s path=%s status=%s tempo_ms=%.2f user_id=%s',
                getattr(request, 'method', ''),
                path,
                getattr(response, 'status_code', None),
                elapsed_ms,
                getattr(getattr(request, 'user', None), 'id', None),
            )
        return response


class CatchAllExceptionsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            response = self.get_response(request)
            return response
        except Exception as e:
            print(f"ERRO GLOBAL: {e}")
            logger.exception('ERRO GLOBAL: %s', e)
            raise