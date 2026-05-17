import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.operacional_api import OperacionalAPIView
from apps.core.operacional_transicao import url_lista_separacao
from apps.tarefas.services.separacao_service import (
    SeparacaoError,
    bipar_tarefa,
    finalizar_tarefa,
    iniciar_tarefa,
    listar_tarefas_disponiveis,
)
from apps.usuarios.access import PerfilPermitido
from apps.usuarios.models import Usuario

logger = logging.getLogger(__name__)

OPERACIONAL_STATUS_BLOQUEADO = 'FECHADO_COM_RESTRICAO'
OPERACIONAL_STATUS_BLOQUEADO_ERRO = (
    'FECHADO_COM_RESTRICAO bloqueia a NF e nao envia para conferencia. '
    'Conclua a separacao ou solicite liberacao da gestao.'
)


class ListarTarefasSeparacaoAPIView(OperacionalAPIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.SEPARADOR, Usuario.Perfil.GESTOR)

    def get(self, request):
        return Response(listar_tarefas_disponiveis(request.user), status=status.HTTP_200_OK)


class IniciarTarefaSeparacaoAPIView(OperacionalAPIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.SEPARADOR, Usuario.Perfil.GESTOR)

    def post(self, request):
        tarefa_id = request.data.get('tarefa_id')
        logger.info(
            'ACEITAR_SEPARACAO_REQUEST user_id=%s tarefa_id=%s',
            getattr(request.user, 'id', None),
            tarefa_id,
        )
        if not tarefa_id:
            return Response(
                {'success': False, 'erro': 'Informe o identificador da tarefa.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            resultado = iniciar_tarefa(tarefa_id, request.user)
        except SeparacaoError as exc:
            raise
        except Exception:
            logger.exception('ACEITAR_SEPARACAO_ERROR tarefa_id=%s user_id=%s', tarefa_id, request.user.id)
            raise
        logger.info('ACEITAR_SEPARACAO_OK user_id=%s tarefa_id=%s', request.user.id, tarefa_id)
        return Response(resultado, status=status.HTTP_200_OK)


class BiparTarefaSeparacaoAPIView(OperacionalAPIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.SEPARADOR, Usuario.Perfil.GESTOR)

    def post(self, request):
        try:
            resultado = bipar_tarefa(request.data.get('tarefa_id'), request.data.get('codigo'), request.user)
        except SeparacaoError as exc:
            raise
        return Response(resultado, status=status.HTTP_200_OK)


class ProximaTarefaSeparacaoAPIView(OperacionalAPIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.SEPARADOR, Usuario.Perfil.GESTOR)

    def get(self, request):
        return Response(
            {
                'tem_proxima': False,
                'proxima_tarefa_id': None,
                'redirect_url': url_lista_separacao(),
            },
            status=status.HTTP_200_OK,
        )


class FinalizarTarefaSeparacaoAPIView(OperacionalAPIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.SEPARADOR, Usuario.Perfil.GESTOR)

    def post(self, request):
        if request.data.get('status') == OPERACIONAL_STATUS_BLOQUEADO:
            return Response({'success': False, 'erro': OPERACIONAL_STATUS_BLOQUEADO_ERRO}, status=status.HTTP_400_BAD_REQUEST)
        try:
            resultado = finalizar_tarefa(
                request.data.get('tarefa_id'),
                request.data.get('status'),
                request.user,
                request.data.get('motivo'),
            )
        except SeparacaoError as exc:
            raise
        return Response(resultado, status=status.HTTP_200_OK)
