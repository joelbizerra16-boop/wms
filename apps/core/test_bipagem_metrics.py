import logging

from django.test import SimpleTestCase, override_settings

from apps.core.operacional_bipagem_metrics import BipagemMetrics


class BipagemMetricsTestCase(SimpleTestCase):
    def test_registrar_emite_campos_obrigatorios(self):
        with self.assertLogs('apps.core.operacional_bipagem_metrics', level='INFO') as captured:
            metricas = BipagemMetrics('separacao', 10, 1)
            with metricas.fase('lock'):
                pass
            with metricas.fase('query'):
                pass
            with metricas.fase('save'):
                pass
            with metricas.fase('response'):
                pass
            metricas.registrar()

        mensagem = captured.records[-1].getMessage()
        for token in (
            'BIPAGEM_TOTAL_MS',
            'modulo=separacao',
            'total_ms=',
            'query_ms=',
            'lock_ms=',
            'save_ms=',
            'response_ms=',
        ):
            self.assertIn(token, mensagem)

    @override_settings(BIPAGEM_METRICS_ENABLED=False)
    def test_registrar_desligado_por_setting(self):
        with self.assertRaises(AssertionError):
            with self.assertLogs('apps.core.operacional_bipagem_metrics', level='INFO'):
                BipagemMetrics('conferencia', 2, 3).registrar()

    def test_fase_soma_mesmo_nome(self):
        metricas = BipagemMetrics('separacao', 1, 1)
        with metricas.fase('lock'):
            pass
        with metricas.fase('lock'):
            pass
        self.assertGreater(metricas._ms('lock'), 0.0)
