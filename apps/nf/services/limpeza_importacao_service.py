from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.conferencia.models import Conferencia
from apps.nf.models import EntradaNF, NotaFiscal
from apps.tarefas.models import Tarefa


class LimpezaImportacaoError(Exception):
	pass


@dataclass
class LimpezaImportacaoResultado:
	registros_entrada_removidos: int
	notas_removidas: int
	periodo_inicio: timezone.datetime
	periodo_fim: timezone.datetime


STATUS_TAREFA_ATIVOS = {
	Tarefa.Status.ABERTO,
	Tarefa.Status.EM_EXECUCAO,
	Tarefa.Status.LIBERADO_COM_RESTRICAO,
}
STATUS_CONFERENCIA_ATIVOS = {
	Conferencia.Status.AGUARDANDO,
	Conferencia.Status.EM_CONFERENCIA,
	Conferencia.Status.DIVERGENCIA,
	Conferencia.Status.LIBERADO_COM_RESTRICAO,
}
STATUS_NF_PERMITIDO_EXCLUSAO = {
	NotaFiscal.Status.CONCLUIDO,
	NotaFiscal.Status.CONCLUIDO_COM_RESTRICAO,
	NotaFiscal.Status.BLOQUEADA_COM_RESTRICAO,
}


def executar_limpeza_importacao_controlada():
	now = timezone.now()
	limite_60_dias = now - timedelta(days=60)

	entrada_mais_antiga = EntradaNF.objects.order_by('data_importacao', 'id').first()
	if entrada_mais_antiga is None or entrada_mais_antiga.data_importacao > limite_60_dias:
		raise LimpezaImportacaoError(
			'Não há dados suficientes para realizar a limpeza. É necessário possuir mais de 60 dias de dados.'
		)

	periodo_inicio = entrada_mais_antiga.data_importacao
	periodo_fim = min(periodo_inicio + timedelta(days=30), limite_60_dias)

	entradas_candidatas = list(
		EntradaNF.objects.filter(
			data_importacao__gte=periodo_inicio,
			data_importacao__lte=periodo_fim,
		).order_by('data_importacao', 'id')
	)
	if not entradas_candidatas:
		raise LimpezaImportacaoError('Nenhuma entrada elegível para limpeza no período selecionado.')

	chaves_nf = [entrada.chave_nf for entrada in entradas_candidatas if entrada.chave_nf]
	_validar_integridade_banco()
	_validar_vinculos_ativos(chaves_nf)

	nfs_para_remover = list(
		NotaFiscal.objects.filter(chave_nfe__in=chaves_nf)
		.filter(status__in=STATUS_NF_PERMITIDO_EXCLUSAO)
		.filter(
			Q(status_fiscal__in=[NotaFiscal.StatusFiscal.CANCELADA, NotaFiscal.StatusFiscal.DENEGADA])
			| Q(ativa=False)
		)
	)

	with transaction.atomic():
		registros_entrada_removidos = 0
		for entrada in entradas_candidatas:
			if entrada.xml:
				entrada.xml.delete(save=False)
			entrada.delete()
			registros_entrada_removidos += 1

		notas_removidas = len(nfs_para_remover)
		if notas_removidas:
			NotaFiscal.objects.filter(id__in=[nf.id for nf in nfs_para_remover]).delete()

	return LimpezaImportacaoResultado(
		registros_entrada_removidos=registros_entrada_removidos,
		notas_removidas=notas_removidas,
		periodo_inicio=periodo_inicio,
		periodo_fim=periodo_fim,
	)


def _validar_vinculos_ativos(chaves_nf):
	if not chaves_nf:
		return

	nfs = NotaFiscal.objects.filter(chave_nfe__in=chaves_nf)
	if not nfs.exists():
		return

	tarefa_ativa = (
		Tarefa.objects.filter(Q(nf__in=nfs) | Q(itens__nf__in=nfs), ativo=True, status__in=STATUS_TAREFA_ATIVOS)
		.distinct()
		.exists()
	)
	if tarefa_ativa:
		raise LimpezaImportacaoError(
			'Limpeza bloqueada: existem tarefas em andamento vinculadas aos XMLs selecionados.'
		)

	conferencia_ativa = Conferencia.objects.filter(nf__in=nfs, status__in=STATUS_CONFERENCIA_ATIVOS).exists()
	if conferencia_ativa:
		raise LimpezaImportacaoError(
			'Limpeza bloqueada: existem conferências ativas vinculadas aos XMLs selecionados.'
		)


def _validar_integridade_banco():
	duplicadas = (
		NotaFiscal.objects.values('chave_nfe')
		.annotate(total=Count('id'))
		.filter(total__gt=1)
		.exists()
	)
	if duplicadas:
		raise LimpezaImportacaoError('Limpeza bloqueada: integridade inválida (existem NFs duplicadas).')

	cancelada_ativa = NotaFiscal.objects.filter(
		status_fiscal=NotaFiscal.StatusFiscal.CANCELADA,
		ativa=True,
	).exists()
	if cancelada_ativa:
		raise LimpezaImportacaoError('Limpeza bloqueada: integridade inválida (NF cancelada ativa).')
