from rest_framework.routers import DefaultRouter

from apps.clientes.views import ClienteViewSet


router = DefaultRouter()
router.register('', ClienteViewSet, basename='cliente')

urlpatterns = router.urls