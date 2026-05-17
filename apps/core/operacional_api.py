"""Contrato JSON consistente para APIs operacionais (pocket / fetch)."""

import logging

from django.http import JsonResponse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


def requisicao_operacional_api(request):
    path = (getattr(request, 'path', None) or '').lower()
    if path.startswith('/api/'):
        return True
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    accept = (request.headers.get('Accept') or '').lower()
    return 'application/json' in accept


def resposta_json_operacional(payload, *, status_code=200):
    body = {'success': status_code < 400}
    body.update(payload)
    return JsonResponse(body, status=status_code)


def csrf_failure_json_ou_html(request, reason=''):
    """Usado por CSRF_FAILURE_VIEW para não quebrar fetch do pocket."""
    from apps.usuarios.views import MENSAGEM_CSRF_EXPIRADO

    logger.warning(
        'CSRF_FAILURE path=%s reason=%s ajax=%s',
        request.path,
        reason,
        requisicao_operacional_api(request),
    )
    if requisicao_operacional_api(request):
        return resposta_json_operacional(
            {
                'erro': MENSAGEM_CSRF_EXPIRADO,
                'session_expired': True,
                'csrf_failed': True,
            },
            status_code=403,
        )

    from apps.usuarios.views import csrf_failure

    return csrf_failure(request, reason)


class OperacionalAPIView(APIView):
    """Garante respostas JSON e tratamento de erros em endpoints de separação/conferência."""

    def handle_exception(self, exc):
        from apps.conferencia.services.conferencia_service import ConferenciaError
        from apps.tarefas.services.separacao_service import SeparacaoError

        if isinstance(exc, (SeparacaoError, ConferenciaError)):
            codigo = status.HTTP_409_CONFLICT if 'em uso' in str(exc).lower() else status.HTTP_400_BAD_REQUEST
            logger.warning('OPERACIONAL_API_NEGOCIO view=%s erro=%s', self.__class__.__name__, exc)
            return Response(
                {'success': False, 'status': 'erro', 'erro': str(exc), 'mensagem': str(exc)},
                status=codigo,
            )

        if hasattr(exc, 'status_code') and hasattr(exc, 'detail'):
            resposta = super().handle_exception(exc)
            if resposta is not None and isinstance(resposta.data, dict):
                resposta.data.setdefault('success', False)
            return resposta

        logger.exception('OPERACIONAL_API_ERRO view=%s', self.__class__.__name__)
        return Response(
            {'success': False, 'status': 'erro', 'erro': 'Erro interno ao processar operação.'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    def finalize_response(self, request, response, *args, **kwargs):
        resposta = super().finalize_response(request, response, *args, **kwargs)
        if isinstance(resposta.data, dict):
            if resposta.status_code < 400:
                resposta.data.setdefault('success', True)
                resposta.data.setdefault('status', 'ok')
            else:
                resposta.data.setdefault('success', False)
        return resposta
