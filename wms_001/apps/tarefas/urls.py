from rest_framework.routers import DefaultRouter

from apps.tarefas.views import TarefaItemViewSet, TarefaViewSet


router = DefaultRouter()
router.register('', TarefaViewSet, basename='tarefa')
router.register('itens', TarefaItemViewSet, basename='tarefa-item')

urlpatterns = router.urls