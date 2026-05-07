from django.shortcuts import render
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.usuarios.access import build_access_context, require_profiles
from apps.usuarios.models import Usuario


class HealthCheckView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({'status': 'ok'}, status=status.HTTP_200_OK)


@require_profiles(Usuario.Perfil.GESTOR)
def home(request):
    context = {
        'usuario': request.user,
        'modulos_operacionais': [
            'Separacao',
            'Conferencia',
            'Controle operacional',
            'Gestao de XML',
            'Controle de setores',
            'Gestao logistica',
        ],
    }
    context.update(build_access_context(request.user))
    return render(request, 'home.html', context)
