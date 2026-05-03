from rest_framework.routers import DefaultRouter

from apps.nf.views import NotaFiscalItemViewSet, NotaFiscalViewSet


router = DefaultRouter()
router.register('', NotaFiscalViewSet, basename='nota-fiscal')
router.register('itens', NotaFiscalItemViewSet, basename='nota-fiscal-item')

urlpatterns = router.urls