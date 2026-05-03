from rest_framework.routers import DefaultRouter

from apps.logs.views import LogViewSet


router = DefaultRouter()
router.register('', LogViewSet, basename='log')

urlpatterns = router.urls