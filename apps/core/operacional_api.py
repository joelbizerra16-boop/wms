"""Contrato JSON consistente para APIs operacionais (pocket / fetch)."""

import logging
from copy import deepcopy

from django.http import JsonResponse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

ENVELOPE_KEYS = {'success', 'message', 'data', 'errors', 'redirect', 'reload'}


def _payload_envelopado(payload):
    return isinstance(payload, dict) and ENVELOPE_KEYS.issubset(payload.keys())


def _normalizar_erros(errors, message=''):
    if errors is None:
        return [message] if message else []
    if isinstance(errors, list):
        return [str(item) for item in errors if str(item)]
    if isinstance(errors, dict):
        normalizados = []
        for chave, valor in errors.items():
            if isinstance(valor, list):
                for item in valor:
                    texto = str(item).strip()
                    if texto:
                        normalizados.append(f'{chave}: {texto}')
            else:
                texto = str(valor).strip()
                if texto:
                    normalizados.append(f'{chave}: {texto}')
        return normalizados
    texto = str(errors).strip()
    return [texto] if texto else ([message] if message else [])


def construir_envelope_operacional(*, success, message='', data=None, errors=None, redirect=None, reload=False):
    return {
        'success': bool(success),
        'message': str(message or ''),
        'data': {} if data is None else data,
        'errors': _normalizar_erros(errors, message=str(message or '')),
        'redirect': redirect,
        'reload': bool(reload),
    }


def envelopar_payload_operacional(payload, *, status_code):
    if _payload_envelopado(payload):
        return payload

    success = status_code < 400
    message = ''
    redirect = None
    reload = False
    data = payload
    errors = []

    if isinstance(payload, dict):
        bruto = deepcopy(payload)
        redirect = bruto.pop('redirect', bruto.pop('redirect_url', None))
        reload = bool(bruto.pop('reload', False))
        bruto.pop('success', None)

        if success:
            message = bruto.get('message') or bruto.get('mensagem') or bruto.get('feedback') or ''
            data = bruto
        else:
            message = (
                bruto.pop('message', None)
                or bruto.pop('mensagem', None)
                or bruto.pop('erro', None)
                or bruto.pop('error', None)
                or bruto.pop('detail', None)
                or ''
            )
            errors = bruto.pop('errors', None)
            data = bruto if bruto else {}

    return construir_envelope_operacional(
        success=success,
        message=message,
        data=data,
        errors=errors,
        redirect=redirect,
        reload=reload,
    )


def requisicao_operacional_api(request):
    path = (getattr(request, 'path', None) or '').lower()
    if path.startswith('/api/'):
        return True
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    accept = (request.headers.get('Accept') or '').lower()
    return 'application/json' in accept


def resposta_json_operacional(payload, *, status_code=200):
    body = envelopar_payload_operacional(payload, status_code=status_code)
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
                'message': MENSAGEM_CSRF_EXPIRADO,
                'errors': [MENSAGEM_CSRF_EXPIRADO],
                'data': {
                    'session_expired': True,
                    'csrf_failed': True,
                },
                'reload': True,
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
                construir_envelope_operacional(success=False, message=str(exc), data={}, errors=[str(exc)]),
                status=codigo,
            )

        if hasattr(exc, 'status_code') and hasattr(exc, 'detail'):
            resposta = super().handle_exception(exc)
            if resposta is not None:
                resposta.data = envelopar_payload_operacional(resposta.data, status_code=resposta.status_code)
            return resposta

        logger.exception('OPERACIONAL_API_ERRO view=%s', self.__class__.__name__)
        return Response(
            construir_envelope_operacional(
                success=False,
                message='Erro interno ao processar operação.',
                data={},
                errors=['Erro interno ao processar operação.'],
            ),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    def finalize_response(self, request, response, *args, **kwargs):
        resposta = super().finalize_response(request, response, *args, **kwargs)
        if hasattr(resposta, 'data'):
            resposta.data = envelopar_payload_operacional(resposta.data, status_code=resposta.status_code)
        return resposta
