from django.urls import path

from apps.tarefas.separacao_views import (
    BiparTarefaSeparacaoAPIView,
    FinalizarTarefaSeparacaoAPIView,
    IniciarTarefaSeparacaoAPIView,
    ListarTarefasSeparacaoAPIView,
    ProximaTarefaSeparacaoAPIView,
)


urlpatterns = [
    path('tarefas/', ListarTarefasSeparacaoAPIView.as_view(), name='separacao-tarefas'),
    path('iniciar/', IniciarTarefaSeparacaoAPIView.as_view(), name='separacao-iniciar'),
    path('bipar/', BiparTarefaSeparacaoAPIView.as_view(), name='separacao-bipar'),
    path('finalizar/', FinalizarTarefaSeparacaoAPIView.as_view(), name='separacao-finalizar'),
    path('proxima/', ProximaTarefaSeparacaoAPIView.as_view(), name='separacao-proxima'),
]