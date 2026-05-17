import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from apps.core.operacional_api import OperacionalAPIView

from apps.conferencia.serializers import (
    BiparConferenciaSerializer,
    FinalizarConferenciaSerializer,
    IniciarConferenciaSerializer,
    RegistrarDivergenciaSerializer,
)
from apps.conferencia.services.conferencia_service import (
    ConferenciaError,
    bipar_conferencia,
    finalizar_conferencia,
    iniciar_conferencia,
    listar_nfs_disponiveis,
    registrar_divergencia,
)
from apps.core.operacional_transicao import url_lista_conferencia
from apps.usuarios.access import PerfilPermitido
from apps.usuarios.models import Usuario

logger = logging.getLogger(__name__)


class NFsDisponiveisAPIView(OperacionalAPIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.CONFERENTE, Usuario.Perfil.GESTOR)

    def get(self, request):
        return Response(listar_nfs_disponiveis(request.user), status=status.HTTP_200_OK)


class IniciarConferenciaAPIView(OperacionalAPIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.CONFERENTE, Usuario.Perfil.GESTOR)

    def post(self, request):
        serializer = IniciarConferenciaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        logger.info(
            'ACEITAR_CONFERENCIA_REQUEST user_id=%s nf_id=%s',
            getattr(request.user, 'id', None),
            serializer.validated_data.get('nf_id'),
        )
        try:
            resultado = iniciar_conferencia(serializer.validated_data['nf_id'], request.user)
        except ConferenciaError as exc:
            raise
        return Response(resultado, status=status.HTTP_200_OK)


class BiparConferenciaAPIView(OperacionalAPIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.CONFERENTE, Usuario.Perfil.GESTOR)

    def post(self, request):
        serializer = BiparConferenciaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            resultado = bipar_conferencia(
                serializer.validated_data['conferencia_id'],
                serializer.validated_data['codigo'],
                request.user,
            )
        except ConferenciaError as exc:
            raise
        return Response(resultado, status=status.HTTP_200_OK)


class RegistrarDivergenciaAPIView(OperacionalAPIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.CONFERENTE, Usuario.Perfil.GESTOR)

    def post(self, request):
        serializer = RegistrarDivergenciaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            resultado = registrar_divergencia(
                serializer.validated_data['item_id'],
                serializer.validated_data['motivo'],
                serializer.validated_data.get('observacao'),
                request.user,
            )
        except ConferenciaError as exc:
            return Response({'erro': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(resultado, status=status.HTTP_200_OK)


class ProximaNFConferenciaAPIView(OperacionalAPIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.CONFERENTE, Usuario.Perfil.GESTOR)

    def get(self, request):
        return Response(
            {
                'tem_proxima': False,
                'proxima_nf_id': None,
                'redirect_url': url_lista_conferencia(),
            },
            status=status.HTTP_200_OK,
        )


class FinalizarConferenciaAPIView(OperacionalAPIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.CONFERENTE, Usuario.Perfil.GESTOR)

    def post(self, request):
        serializer = FinalizarConferenciaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            resultado = finalizar_conferencia(serializer.validated_data['conferencia_id'], request.user)
        except ConferenciaError as exc:
            return Response({'erro': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(resultado, status=status.HTTP_200_OK)
