"""Profiling ORM/DB dos fluxos críticos (separação, conferência, produto)."""

import time

from django.core.management.base import BaseCommand
from django.db import connection, reset_queries
from django.test.utils import CaptureQueriesContext

from apps.core.db_telemetry import install_db_execute_wrapper, operacional_db_scope


class Command(BaseCommand):
    help = 'Profile de queries nos serviços operacionais críticos.'

    def add_arguments(self, parser):
        parser.add_argument('--bipagens', type=int, default=3, help='Repetições de bipagem simulada')
        parser.add_argument('--usuario-id', type=int, default=None, help='ID do separador/conferente')

    def handle(self, *args, **options):
        install_db_execute_wrapper()
        from apps.conferencia.models import Conferencia
        from apps.tarefas.models import Tarefa
        from apps.tarefas.services.separacao_service import bipar_tarefa
        from apps.conferencia.services.conferencia_service import bipar_conferencia
        from apps.usuarios.models import Usuario

        usuario = None
        if options['usuario_id']:
            usuario = Usuario.objects.filter(pk=options['usuario_id']).first()
        if usuario is None:
            usuario = Usuario.objects.filter(is_active=True).first()
        if usuario is None:
            self.stderr.write(self.style.ERROR('Nenhum usuário ativo encontrado.'))
            return

        tarefa = (
            Tarefa.objects.filter(status='EM_EXECUCAO', usuario_em_execucao_id=usuario.id)
            .order_by('-id')
            .first()
        )
        conferencia = (
            Conferencia.objects.filter(status='EM_CONFERENCIA', conferente_id=usuario.id)
            .order_by('-id')
            .first()
        )

        relatorio = []

        if tarefa:
            relatorio.append(self._profile_bipagem_separacao(tarefa.id, usuario, options['bipagens']))
        else:
            relatorio.append({'modulo': 'separacao', 'erro': 'sem tarefa EM_EXECUCAO'})

        if conferencia:
            relatorio.append(self._profile_bipagem_conferencia(conferencia.id, usuario, options['bipagens']))
        else:
            relatorio.append({'modulo': 'conferencia', 'erro': 'sem conferencia EM_CONFERENCIA'})

        for bloco in relatorio:
            self.stdout.write(str(bloco))

    def _profile_bipagem_separacao(self, tarefa_id, usuario, repeticoes):
        from django.db.models import F

        from apps.core.services.produto_validacao_service import _codigo_exibicao_produto
        from apps.tarefas.models import TarefaItem
        from apps.tarefas.services.separacao_service import bipar_tarefa

        item = (
            TarefaItem.objects.filter(tarefa_id=tarefa_id, quantidade_separada__lt=F('quantidade_total'))
            .select_related('produto')
            .first()
        )
        if item is None:
            return {'modulo': 'separacao', 'erro': 'sem item pendente'}
        codigo = _codigo_exibicao_produto(item.produto) or item.produto.cod_ean or item.produto.cod_prod

        tempos = []
        queries_total = []
        for _ in range(repeticoes):
            reset_queries()
            inicio = time.perf_counter()
            with operacional_db_scope('separacao', 'db_profile_bipar'):
                with CaptureQueriesContext(connection) as ctx:
                    try:
                        bipar_tarefa(tarefa_id, codigo, usuario)
                    except Exception as exc:
                        return {'modulo': 'separacao', 'erro': str(exc)}
                    queries_total.append(len(ctx.captured_queries))
            tempos.append((time.perf_counter() - inicio) * 1000)

        return {
            'modulo': 'separacao',
            'tarefa_id': tarefa_id,
            'codigo': codigo,
            'repeticoes': repeticoes,
            'bipagem_ms_media': round(sum(tempos) / len(tempos), 2),
            'bipagem_ms_max': round(max(tempos), 2),
            'queries_media': round(sum(queries_total) / len(queries_total), 2),
            'queries_max': max(queries_total),
        }

    def _profile_bipagem_conferencia(self, conferencia_id, usuario, repeticoes):
        from django.db.models import F

        from apps.conferencia.models import ConferenciaItem
        from apps.core.services.produto_validacao_service import _codigo_exibicao_produto
        item = (
            ConferenciaItem.objects.filter(
                conferencia_id=conferencia_id,
                status='AGUARDANDO',
                qtd_conferida__lt=F('qtd_esperada'),
            )
            .select_related('produto')
            .first()
        )
        if item is None:
            return {'modulo': 'conferencia', 'erro': 'sem item pendente'}
        codigo = _codigo_exibicao_produto(item.produto) or item.produto.cod_ean or item.produto.cod_prod

        tempos = []
        queries_total = []
        for _ in range(repeticoes):
            reset_queries()
            inicio = time.perf_counter()
            with operacional_db_scope('conferencia', 'db_profile_bipar'):
                with CaptureQueriesContext(connection) as ctx:
                    try:
                        bipar_conferencia(conferencia_id, codigo, usuario)
                    except Exception as exc:
                        return {'modulo': 'conferencia', 'erro': str(exc)}
                    queries_total.append(len(ctx.captured_queries))
            tempos.append((time.perf_counter() - inicio) * 1000)

        return {
            'modulo': 'conferencia',
            'conferencia_id': conferencia_id,
            'codigo': codigo,
            'repeticoes': repeticoes,
            'bipagem_ms_media': round(sum(tempos) / len(tempos), 2),
            'bipagem_ms_max': round(max(tempos), 2),
            'queries_media': round(sum(queries_total) / len(queries_total), 2),
            'queries_max': max(queries_total),
        }
