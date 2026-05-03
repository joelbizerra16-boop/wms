from rest_framework.routers import DefaultRouter

from apps.rotas.views import RotaViewSet


router = DefaultRouter()
router.register('', RotaViewSet, basename='rota')

urlpatterns = router.urls