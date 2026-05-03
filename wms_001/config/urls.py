from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path, re_path
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework import permissions

from apps.conferencia.views_web import aceitar_conferencia_web, conferencia_lista_web, conferir_nf, registrar_divergencia_web
from apps.core.views_dashboard import dashboard_conferencia, dashboard_separacao, detalhe_nf, detalhe_nf_por_id, relatorio_liberacoes
from apps.core.views import dashboard_data, home
from apps.core.views_liberacao import (
    excluir_nf_conferencia_view,
    excluir_tarefa_view,
    liberar_nf_divergencia_view,
    liberar_tarefa_divergencia_view,
)
from apps.core.views_web import (
    ativacao_scan_nfs_web,
    clientes_web,
    editar_usuario_web,
    excluir_usuario_web,
    fila_entradas_nf_web,
    importar_xml_web,
    confirmar_scan_entradas_web,
    liberar_entrada_nf_web,
    produtos_web,
    remover_scan_nf_api,
    rotas_web,
    separacao_exec_web,
    separacao_lista_web,
    toggle_usuario_status,
    usuarios_web,
    scan_nf_api,
)
from apps.core.views_produtividade import (
    produtividade_dashboard,
    produtividade_export_excel,
    produtividade_export_pdf,
    produtividade_ranking,
    produtividade_relatorio,
)
from apps.usuarios.views import forcar_logout_usuario, login_view, logout_view, usuarios_logados

schema_view = get_schema_view(
    openapi.Info(
        title='WMS API',
        default_version='v1',
        description='Documentacao inicial da API do WMS.',
    ),
    public=True,
    permission_classes=(permissions.AllowAny,),
)

urlpatterns = [
    path('', lambda request: redirect('login')),
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('home/', home, name='home'),
    path('api/dashboard/', dashboard_data, name='api-dashboard'),
    path('importar/', importar_xml_web, name='web-importar-xml'),
    path('importar/fila/', fila_entradas_nf_web, name='web-fila-entradas-nf'),
    path('importar/fila/<int:entrada_id>/liberar/', liberar_entrada_nf_web, name='web-liberar-entrada-nf'),
    path('importar/ativacao-scan/', ativacao_scan_nfs_web, name='web-ativacao-scan-nf'),
    path('importar/ativacao-scan/confirmar/', confirmar_scan_entradas_web, name='web-confirmar-scan-entradas'),
    path('api/scan-nf/', scan_nf_api, name='api-scan-nf'),
    path('api/scan-nf/remover/', remover_scan_nf_api, name='api-remover-scan-nf'),
    path('dashboard/separacao/', dashboard_separacao, name='web-dashboard-separacao'),
    path('dashboard/conferencia/', dashboard_conferencia, name='web-dashboard-conferencia'),
    path('relatorio/liberacoes/', relatorio_liberacoes, name='web-relatorio-liberacoes'),
    path('separacao/', separacao_lista_web, name='web-separacao-lista'),
    path('separacao/<int:tarefa_id>/', separacao_exec_web, name='web-separacao-exec'),
    path('liberacao/tarefa/<int:tarefa_id>/', liberar_tarefa_divergencia_view, name='web-liberar-tarefa-divergencia'),
    path('tarefas/excluir/<int:tarefa_id>/', excluir_tarefa_view, name='web-excluir-tarefa'),
    path('conferencia/', conferencia_lista_web, name='web-conferencia-lista'),
    path('conferencia/detalhe/<str:nf_numero>/', detalhe_nf, name='web-conferencia-detalhe'),
    path('conferencia/detalhe-id/<int:nf_id>/', detalhe_nf_por_id, name='web-conferencia-detalhe-id'),
    path('conferencia/aceitar/<int:nf_id>/', aceitar_conferencia_web, name='aceitar_conferencia'),
    path('conferencia/<int:nf_id>/', conferir_nf, name='web-conferencia-exec'),
    path('conferencia/divergencia/<int:item_id>/', registrar_divergencia_web, name='web-conferencia-divergencia'),
    path('liberacao/nf/<int:nf_id>/', liberar_nf_divergencia_view, name='web-liberar-nf-divergencia'),
    path('conferencia/excluir/<int:nf_id>/', excluir_nf_conferencia_view, name='web-excluir-conferencia'),
    path('clientes/', clientes_web, name='web-clientes'),
    path('produtos/', produtos_web, name='web-produtos'),
    path('rotas/', rotas_web, name='web-rotas'),
    path('usuarios/', usuarios_web, name='web-usuarios'),
    path('usuarios/<int:user_id>/editar/', editar_usuario_web, name='editar_usuario'),
    path('usuarios/<int:user_id>/excluir/', excluir_usuario_web, name='excluir_usuario'),
    path('usuarios/<int:user_id>/toggle-status/', toggle_usuario_status, name='web-toggle-usuario-status'),
    path('usuarios/logados/', usuarios_logados, name='usuarios_logados'),
    path('usuarios/logados/forcar-logout/<int:usuario_id>/', forcar_logout_usuario, name='forcar_logout_usuario'),
    path('produtividade/dashboard/', produtividade_dashboard, name='web-produtividade-dashboard'),
    path('produtividade/relatorio/', produtividade_relatorio, name='web-produtividade-relatorio'),
    path('produtividade/ranking/', produtividade_ranking, name='web-produtividade-ranking'),
    path('produtividade/export/excel/', produtividade_export_excel, name='web-produtividade-export-excel'),
    path('produtividade/export/pdf/', produtividade_export_pdf, name='web-produtividade-export-pdf'),
    path('admin/', admin.site.urls),
    path('api/', include('apps.core.urls')),
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    re_path(r'^swagger(?P<format>\.json|\.yaml)$', schema_view.without_ui(cache_timeout=0), name='schema-json'),
]
