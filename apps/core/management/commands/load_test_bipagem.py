"""Teste de carga focado em bipagem (separação/conferência)."""

import concurrent.futures
import statistics
import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import F

from apps.conferencia.models import Conferencia, ConferenciaItem
from apps.conferencia.services.conferencia_service import bipar_conferencia
from apps.core.db_telemetry import install_db_execute_wrapper, operacional_db_scope
from apps.core.services.produto_validacao_service import _codigo_exibicao_produto
from apps.tarefas.models import TarefaItem
from apps.tarefas.services.separacao_service import bipar_tarefa


class Command(BaseCommand):
    help = 'Simula bipagens concorrentes para medir latência e throughput.'

    def add_arguments(self, parser):
        parser.add_argument('--workers', type=int, default=20)
        parser.add_argument('--bipagens', type=int, default=10, help='Bipagens por worker')
        parser.add_argument('--modulo', choices=('separacao', 'conferencia', 'both'), default='both')

    def handle(self, *args, **options):
        install_db_execute_wrapper()
        usuario_model = get_user_model()
        usuario = usuario_model.objects.filter(is_active=True).first()
        if usuario is None:
            self.stderr.write(self.style.ERROR('Sem usuário ativo.'))
            return

        jobs = []
        if options['modulo'] in ('separacao', 'both'):
            item = (
                TarefaItem.objects.filter(
                    tarefa__status='EM_EXECUCAO',
                    tarefa__usuario_em_execucao_id=usuario.id,
                    quantidade_separada__lt=F('quantidade_total'),
                )
                .select_related('produto', 'tarefa')
                .first()
            )
            if item:
                codigo = _codigo_exibicao_produto(item.produto) or item.produto.cod_ean
                jobs.append(('separacao', item.tarefa_id, codigo))
        if options['modulo'] in ('conferencia', 'both'):
            conf = Conferencia.objects.filter(status='EM_CONFERENCIA', conferente_id=usuario.id).first()
            if conf:
                citem = (
                    ConferenciaItem.objects.filter(
                        conferencia_id=conf.id,
                        status='AGUARDANDO',
                        qtd_conferida__lt=F('qtd_esperada'),
                    )
                    .select_related('produto')
                    .first()
                )
                if citem:
                    codigo = _codigo_exibicao_produto(citem.produto) or citem.produto.cod_ean
                    jobs.append(('conferencia', conf.id, codigo))

        if not jobs:
            self.stderr.write(self.style.ERROR('Sem tarefa/conferência ativa para testar.'))
            return

        tempos = []

        def _worker(worker_id):
            modulo, entidade_id, codigo = jobs[worker_id % len(jobs)]
            for _ in range(options['bipagens']):
                inicio = time.perf_counter()
                with operacional_db_scope(modulo, 'load_test_bipar'):
                    try:
                        if modulo == 'separacao':
                            bipar_tarefa(entidade_id, codigo, usuario)
                        else:
                            bipar_conferencia(entidade_id, codigo, usuario)
                    except Exception as exc:
                        self.stderr.write(f'worker={worker_id} erro={exc}')
                tempos.append((time.perf_counter() - inicio) * 1000)

        inicio_total = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=options['workers']) as pool:
            list(pool.map(_worker, range(options['workers'])))
        total_s = time.perf_counter() - inicio_total
        total_bip = len(tempos)
        throughput = total_bip / total_s if total_s else 0
        p95 = statistics.quantiles(tempos, n=20)[-1] if len(tempos) >= 20 else max(tempos)

        self.stdout.write(
            self.style.SUCCESS(
                f'BIPAGEM_LOAD_TEST workers={options["workers"]} bipagens={total_bip} '
                f'tempo_total={total_s:.2f}s throughput={throughput:.1f}/s '
                f'media={statistics.mean(tempos):.1f}ms mediana={statistics.median(tempos):.1f}ms '
                f'p95={p95:.1f}ms max={max(tempos):.1f}ms'
            )
        )
