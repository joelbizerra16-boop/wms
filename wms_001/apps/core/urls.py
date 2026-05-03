from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token

from apps.core.views import HealthCheckView
from apps.core.views_status import DashboardResumoAPIView, StatusNFAPIView, StatusTarefaAPIView
from apps.nf.views import ImportarXMLAPIView


urlpatterns = [
    path('health/', HealthCheckView.as_view(), name='health-check'),
    path('status/nf/<int:nf_id>/', StatusNFAPIView.as_view(), name='status-nf'),
    path('status/tarefa/<int:tarefa_id>/', StatusTarefaAPIView.as_view(), name='status-tarefa'),
    path('tarefa-status/<int:tarefa_id>/', StatusTarefaAPIView.as_view(), name='api-tarefa-status'),
    path('dashboard/resumo/', DashboardResumoAPIView.as_view(), name='dashboard-resumo'),
    path('auth/token/', obtain_auth_token, name='auth-token'),
    path('importar-xml/', ImportarXMLAPIView.as_view(), name='importar-xml'),
    path('conferencia/', include('apps.conferencia.urls')),
    path('separacao/', include('apps.tarefas.separacao_urls')),
    path('usuarios/', include('apps.usuarios.urls')),
    path('clientes/', include('apps.clientes.urls')),
    path('produtos/', include('apps.produtos.urls')),
    path('rotas/', include('apps.rotas.urls')),
    path('notas-fiscais/', include('apps.nf.urls')),
    path('tarefas/', include('apps.tarefas.urls')),
    path('logs/', include('apps.logs.urls')),
]
