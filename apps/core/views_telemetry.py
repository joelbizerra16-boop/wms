"""Painel técnico JSON de telemetria operacional (gestor)."""

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View

from apps.core.db_telemetry import obter_snapshot_telemetria
from apps.usuarios.access import require_profiles
from apps.usuarios.models import Usuario


@method_decorator(require_profiles(Usuario.Perfil.GESTOR), name='dispatch')
class TelemetriaOperacionalAPIView(View):
    def get(self, request):
        snapshot = obter_snapshot_telemetria()
        itens = sorted(
            snapshot.values(),
            key=lambda item: float(item.get('transaction_ms') or 0),
            reverse=True,
        )
        return JsonResponse(
            {
                'ok': True,
                'operacoes': itens[:50],
                'legenda': {
                    'DB_QUERY_MS': 'query individual lenta',
                    'DB_TRANSACTION_MS': 'tempo total da operação',
                    'DB_LOCK_MS': 'query com FOR UPDATE',
                    'ORM_N_PLUS_ONE': 'repetição suspeita de SQL',
                    'BIPAGEM_TOTAL_MS': 'tempo total bipagem',
                },
            }
        )
