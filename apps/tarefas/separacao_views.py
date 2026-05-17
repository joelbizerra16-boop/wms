from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tarefas.services.separacao_service import (
    SeparacaoError,
    bipar_tarefa,
    finalizar_tarefa,
    iniciar_tarefa,
    listar_tarefas_disponiveis,
)
from apps.core.operacional_transicao import url_lista_separacao
from apps.usuarios.access import PerfilPermitido
from apps.usuarios.models import Usuario


OPERACIONAL_STATUS_BLOQUEADO = 'FECHADO_COM_RESTRICAO'
OPERACIONAL_STATUS_BLOQUEADO_ERRO = (
    'FECHADO_COM_RESTRICAO bloqueia a NF e nao envia para conferencia. '
    'Conclua a separacao ou solicite liberacao da gestao.'
)


class ListarTarefasSeparacaoAPIView(APIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.SEPARADOR, Usuario.Perfil.GESTOR)

    def get(self, request):
        return Response(listar_tarefas_disponiveis(request.user), status=status.HTTP_200_OK)


class IniciarTarefaSeparacaoAPIView(APIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.SEPARADOR, Usuario.Perfil.GESTOR)

    def post(self, request):
        try:
            resultado = iniciar_tarefa(request.data.get('tarefa_id'), request.user)
        except SeparacaoError as exc:
            return Response({'erro': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(resultado, status=status.HTTP_200_OK)


class BiparTarefaSeparacaoAPIView(APIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.SEPARADOR, Usuario.Perfil.GESTOR)

    def post(self, request):
        try:
            resultado = bipar_tarefa(request.data.get('tarefa_id'), request.data.get('codigo'), request.user)
        except SeparacaoError as exc:
            return Response(
                {'status': 'erro', 'mensagem': str(exc), 'erro': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(resultado, status=status.HTTP_200_OK)


class ProximaTarefaSeparacaoAPIView(APIView):
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


class FinalizarTarefaSeparacaoAPIView(APIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.SEPARADOR, Usuario.Perfil.GESTOR)

    def post(self, request):
        if request.data.get('status') == OPERACIONAL_STATUS_BLOQUEADO:
            return Response({'erro': OPERACIONAL_STATUS_BLOQUEADO_ERRO}, status=status.HTTP_400_BAD_REQUEST)
        try:
            resultado = finalizar_tarefa(
                request.data.get('tarefa_id'),
                request.data.get('status'),
                request.user,
                request.data.get('motivo'),
            )
        except SeparacaoError as exc:
            return Response({'erro': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(resultado, status=status.HTTP_200_OK)