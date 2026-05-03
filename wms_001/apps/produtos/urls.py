from rest_framework.routers import DefaultRouter

from apps.produtos.views import ProdutoViewSet


router = DefaultRouter()
router.register('', ProdutoViewSet, basename='produto')

urlpatterns = router.urls