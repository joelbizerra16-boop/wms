"""Teste de carga focado em bipagem (separação/conferência)."""

import concurrent.futures
import json
import statistics
import time
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import F
from django.utils import timezone

from apps.clientes.models import Cliente
from apps.conferencia.models import Conferencia, ConferenciaItem
from apps.conferencia.services.conferencia_service import bipar_conferencia
from apps.core.db_telemetry import install_db_execute_wrapper, operacional_db_scope
from apps.core.services.produto_validacao_service import _codigo_exibicao_produto
from apps.nf.models import NotaFiscal, NotaFiscalItem
from apps.produtos.models import Produto
from apps.rotas.models import Rota
from apps.tarefas.models import Tarefa, TarefaItem
from apps.tarefas.services.separacao_service import bipar_tarefa
from apps.usuarios.models import Setor, Usuario


def _percentil(valores, pct):
    if not valores:
        return 0.0
    if len(valores) < 2:
        return float(valores[0])
    ordenado = sorted(valores)
    idx = int(round((pct / 100.0) * (len(ordenado) - 1)))
    return float(ordenado[idx])


class Command(BaseCommand):
    help = 'Simula bipagens concorrentes para medir latência e throughput (p50/p95/p99).'

    def add_arguments(self, parser):
        parser.add_argument('--workers', type=int, default=20)
        parser.add_argument('--bipagens', type=int, default=10, help='Bipagens por worker')
        parser.add_argument('--modulo', choices=('separacao', 'conferencia', 'both'), default='both')
        parser.add_argument(
            '--synthetic',
            action='store_true',
            help='Cria cenário efêmero (não usar em produção com dados reais)',
        )
        parser.add_argument('--output', default='', help='JSON opcional com métricas')

    def handle(self, *args, **options):
        usuario_model = get_user_model()
        usuario = usuario_model.objects.filter(is_active=True).first()
        if usuario is None:
            self.stderr.write(self.style.ERROR('Sem usuário ativo.'))
            return

        jobs = []
        cleanup = None
        if options['synthetic']:
            jobs, cleanup = self._criar_cenario_sintetico(usuario)
        else:
            jobs = self._jobs_operacionais(usuario, options['modulo'])

        # Telemetria após setup do cenário: wrapper quebra introspecção SQLite em NotaFiscal.
        install_db_execute_wrapper()
        workers = options['workers']
        if connection.vendor == 'sqlite' and workers > 1:
            self.stdout.write(
                self.style.WARNING('SQLite local: forçando workers=1 (sem concorrência real).')
            )
            workers = 1

        if not jobs:
            self.stderr.write(self.style.ERROR('Sem tarefa/conferência ativa para testar. Use --synthetic.'))
            return

        tempos = []
        erros = 0

        def _worker(worker_id):
            nonlocal erros
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
                        erros += 1
                        self.stderr.write(f'worker={worker_id} erro={exc}')
                tempos.append((time.perf_counter() - inicio) * 1000)

        inicio_total = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(_worker, range(workers)))
        total_s = time.perf_counter() - inicio_total

        if cleanup:
            cleanup()

        if not tempos:
            self.stderr.write(self.style.ERROR('Nenhuma bipagem medida.'))
            return

        p50 = _percentil(tempos, 50)
        p95 = _percentil(tempos, 95)
        p99 = _percentil(tempos, 99)
        total_bip = len(tempos)
        throughput = total_bip / total_s if total_s else 0

        metas = {
            'p50_ok': p50 < 80,
            'p95_ok': p95 < 180,
            'p99_ok': p99 < 300,
        }
        relatorio = {
            'workers': workers,
            'bipagens_total': total_bip,
            'erros': erros,
            'tempo_total_s': round(total_s, 2),
            'throughput_por_s': round(throughput, 2),
            'p50_ms': round(p50, 2),
            'p95_ms': round(p95, 2),
            'p99_ms': round(p99, 2),
            'media_ms': round(statistics.mean(tempos), 2),
            'max_ms': round(max(tempos), 2),
            'metas': metas,
        }

        self.stdout.write(
            self.style.SUCCESS(
                f'BIPAGEM_LOAD_TEST workers={workers} bipagens={total_bip} erros={erros} '
                f'tempo_total={total_s:.2f}s throughput={throughput:.1f}/s '
                f'p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms max={max(tempos):.1f}ms '
                f'metas_p50={metas["p50_ok"]} metas_p95={metas["p95_ok"]} metas_p99={metas["p99_ok"]}'
            )
        )

        if options['output']:
            path = Path(options['output'])
            if not path.is_absolute():
                path = Path(settings.BASE_DIR) / path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding='utf-8')
            self.stdout.write(f'JSON: {path}')

    def _jobs_operacionais(self, usuario, modulo):
        jobs = []
        if modulo in ('separacao', 'both'):
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
        if modulo in ('conferencia', 'both'):
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
        return jobs

    def _criar_cenario_sintetico(self, usuario):
        sufixo = str(int(time.time() * 1000) % 10_000_000)
        rota = Rota.objects.create(
            nome=f'LT-ROTA-{sufixo}',
            cep_inicial='01000000',
            cep_final='01999999',
        )
        cliente = Cliente.objects.create(nome='Load Test', inscricao_estadual=f'LT{sufixo}')
        produto = Produto.objects.create(
            cod_prod=f'LT{sufixo}',
            codigo=f'LT{sufixo}',
            descricao='Produto Load Test',
            cod_ean=f'7890000{sufixo.zfill(7)}'[:13],
            setor=Setor.Codigo.FILTROS,
            categoria=Produto.Categoria.FILTROS,
        )
        nf = NotaFiscal.objects.create(
            chave_nfe=f'3525060000000000000055001000009999{sufixo.zfill(5)}00099999'[:44],
            numero=f'LT{sufixo}',
            cliente=cliente,
            rota=rota,
            status=NotaFiscal.Status.PENDENTE,
            data_emissao=timezone.now(),
            status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
        )
        NotaFiscalItem.objects.create(nf=nf, produto=produto, quantidade=Decimal('50'))
        tarefa = Tarefa.objects.create(
            tipo=Tarefa.Tipo.ROTA,
            setor=Setor.Codigo.FILTROS,
            rota=rota,
            status=Tarefa.Status.EM_EXECUCAO,
            usuario=usuario,
            usuario_em_execucao=usuario,
        )
        TarefaItem.objects.create(
            tarefa=tarefa,
            nf=nf,
            produto=produto,
            quantidade_total=Decimal('50'),
            quantidade_separada=Decimal('0'),
        )
        conferencia = Conferencia.objects.create(
            nf=nf,
            conferente=usuario,
            status=Conferencia.Status.EM_CONFERENCIA,
        )
        ConferenciaItem.objects.create(
            conferencia=conferencia,
            produto=produto,
            qtd_esperada=Decimal('10'),
            qtd_conferida=Decimal('0'),
            status=ConferenciaItem.Status.AGUARDANDO,
        )
        codigo = produto.cod_ean

        def _cleanup():
            ConferenciaItem.objects.filter(conferencia=conferencia).delete()
            conferencia.delete()
            TarefaItem.objects.filter(tarefa=tarefa).delete()
            tarefa.delete()
            NotaFiscalItem.objects.filter(nf=nf).delete()
            nf.delete()
            produto.delete()
            cliente.delete()
            rota.delete()

        return [
            ('separacao', tarefa.id, codigo),
            ('conferencia', conferencia.id, codigo),
        ], _cleanup
