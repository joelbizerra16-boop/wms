"""Teste de carga leve para validar endpoints críticos do WMS."""

import concurrent.futures
import statistics
import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.test import Client


class Command(BaseCommand):
    help = 'Executa requisições simultâneas nos endpoints operacionais (bipagem, status, dashboard).'

    def add_arguments(self, parser):
        parser.add_argument('--users', type=int, default=20, help='Quantidade de workers simultâneos')
        parser.add_argument('--rounds', type=int, default=5, help='Rodadas por worker')
        parser.add_argument('--username', default='gestor_perf', help='Usuário autenticado para o teste')

    def handle(self, *args, **options):
        usuario_model = get_user_model()
        usuario = usuario_model.objects.filter(username=options['username']).first()
        if usuario is None:
            self.stderr.write(self.style.ERROR(f"Usuário '{options['username']}' não encontrado."))
            return

        client = Client()
        client.force_login(usuario)

        endpoints = [
            ('GET', '/api/dashboard/resumo/', None),
            ('GET', '/api/separacao/tarefas/', None),
            ('GET', '/api/conferencia/nfs/', None),
        ]

        tempos_ms = []

        def _worker(worker_id):
            local_client = Client()
            local_client.force_login(usuario)
            for _ in range(options['rounds']):
                for metodo, path, body in endpoints:
                    inicio = time.perf_counter()
                    if metodo == 'GET':
                        response = local_client.get(path, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
                    else:
                        response = local_client.post(path, body, content_type='application/json', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
                    elapsed = (time.perf_counter() - inicio) * 1000
                    tempos_ms.append(elapsed)
                    if response.status_code >= 500:
                        self.stderr.write(f'worker={worker_id} path={path} status={response.status_code}')

        inicio_total = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=options['users']) as executor:
            futures = [executor.submit(_worker, i) for i in range(options['users'])]
            concurrent.futures.wait(futures)
        total_s = time.perf_counter() - inicio_total

        if not tempos_ms:
            self.stderr.write(self.style.ERROR('Nenhuma requisição executada.'))
            return

        p95 = statistics.quantiles(tempos_ms, n=20)[-1] if len(tempos_ms) >= 20 else max(tempos_ms)
        self.stdout.write(
            self.style.SUCCESS(
                f"Carga: workers={options['users']} rounds={options['rounds']} "
                f"reqs={len(tempos_ms)} tempo_total={total_s:.2f}s "
                f"media={statistics.mean(tempos_ms):.1f}ms mediana={statistics.median(tempos_ms):.1f}ms "
                f"p95={p95:.1f}ms max={max(tempos_ms):.1f}ms"
            )
        )
