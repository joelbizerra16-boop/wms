from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

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
    obter_proxima_nf_conferencia,
    registrar_divergencia,
)
from apps.core.operacional_transicao import url_exec_conferencia, url_lista_conferencia
from apps.usuarios.access import PerfilPermitido
from apps.usuarios.models import Usuario


class NFsDisponiveisAPIView(APIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.CONFERENTE, Usuario.Perfil.GESTOR)

    def get(self, request):
        return Response(listar_nfs_disponiveis(request.user), status=status.HTTP_200_OK)


class IniciarConferenciaAPIView(APIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.CONFERENTE, Usuario.Perfil.GESTOR)

    def post(self, request):
        serializer = IniciarConferenciaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            resultado = iniciar_conferencia(serializer.validated_data['nf_id'], request.user)
        except ConferenciaError as exc:
            return Response({'erro': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(resultado, status=status.HTTP_200_OK)


class BiparConferenciaAPIView(APIView):
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
            return Response(
                {'status': 'erro', 'mensagem': str(exc), 'erro': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(resultado, status=status.HTTP_200_OK)


class RegistrarDivergenciaAPIView(APIView):
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


class ProximaNFConferenciaAPIView(APIView):
    permission_classes = [IsAuthenticated, PerfilPermitido]
    allowed_profiles = (Usuario.Perfil.CONFERENTE, Usuario.Perfil.GESTOR)

    def get(self, request):
        excluir = request.query_params.get('excluir_nf_id')
        proxima = obter_proxima_nf_conferencia(
            request.user,
            excluir_nf_id=int(excluir) if excluir else None,
        )
        if proxima:
            return Response(
                {
                    'tem_proxima': True,
                    'proxima_nf_id': proxima['id'],
                    'redirect_url': url_exec_conferencia(proxima['id']),
                },
                status=status.HTTP_200_OK,
            )
        return Response(
            {'tem_proxima': False, 'proxima_nf_id': None, 'redirect_url': url_lista_conferencia()},
            status=status.HTTP_200_OK,
        )


class FinalizarConferenciaAPIView(APIView):
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
