from django.urls import path

from apps.conferencia.views import (
	BiparConferenciaAPIView,
	FinalizarConferenciaAPIView,
	IniciarConferenciaAPIView,
	NFsDisponiveisAPIView,
	RegistrarDivergenciaAPIView,
)


urlpatterns = [
	path('nfs/', NFsDisponiveisAPIView.as_view(), name='conferencia-nfs'),
	path('iniciar/', IniciarConferenciaAPIView.as_view(), name='conferencia-iniciar'),
	path('bipar/', BiparConferenciaAPIView.as_view(), name='conferencia-bipar'),
	path('divergencia/', RegistrarDivergenciaAPIView.as_view(), name='conferencia-divergencia'),
	path('finalizar/', FinalizarConferenciaAPIView.as_view(), name='conferencia-finalizar'),
]