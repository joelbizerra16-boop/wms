from decimal import Decimal

from django.db.models import Case, DecimalField, ExpressionWrapper, F, Value, When
from django.db.models.functions import Coalesce, Greatest

from apps.tarefas.models import OndaSeparacao, Tarefa
from apps.usuarios.models import Setor


MAX_NFS_POR_ONDA = 5
ZERO_DECIMAL = Decimal('0.00')
UNIT_DECIMAL = Decimal('1.00')
HUNDRED_DECIMAL = Decimal('100.00')


def normalizar_tipo_embalagem(valor):
	return (str(valor or '').strip().upper() or 'UN')[:20]


def tipo_tarefa_por_setor(setor):
	return Tarefa.Tipo.FILTRO if setor == Setor.Codigo.FILTROS else Tarefa.Tipo.ROTA


def percentual_expr(*, total_expr, bipado_expr):
	return Case(
		When(
			itens_total__gt=0,
			then=ExpressionWrapper(
				(bipado_expr * Value(HUNDRED_DECIMAL)) / total_expr,
				output_field=DecimalField(max_digits=6, decimal_places=2),
			),
		),
		default=Value(ZERO_DECIMAL),
		output_field=DecimalField(max_digits=6, decimal_places=2),
	)


def _cache_chave_onda(*, rota_id, setor, tipo_embalagem):
	return rota_id, setor, tipo_embalagem


def _carregar_nfs_onda(onda):
	return set(onda.nfs.values_list('id', flat=True))


def _obter_onda_aberta_existente(*, rota, setor, tipo_embalagem, nf):
	queryset = (
		OndaSeparacao.objects.filter(
			rota=rota,
			setor=setor,
			tipo_embalagem=tipo_embalagem,
			status__in=[
				OndaSeparacao.Status.PENDENTE,
				OndaSeparacao.Status.EM_SEPARACAO,
				OndaSeparacao.Status.PARCIAL,
			],
		)
		.order_by('-id')
	)
	for onda in queryset[:10]:
		nf_ids = _carregar_nfs_onda(onda)
		if nf.id in nf_ids or len(nf_ids) < MAX_NFS_POR_ONDA:
			return onda, nf_ids
	return None, set()


def obter_ou_criar_tarefa_onda(*, nf, rota, setor, tipo_embalagem, tarefas_lote_cache=None):
	tipo_embalagem = normalizar_tipo_embalagem(tipo_embalagem)
	cache = tarefas_lote_cache if tarefas_lote_cache is not None else {}
	chave = _cache_chave_onda(rota_id=rota.id, setor=setor, tipo_embalagem=tipo_embalagem)
	cache_item = cache.get(chave)
	if cache_item is not None and (nf.id in cache_item['nf_ids'] or len(cache_item['nf_ids']) < MAX_NFS_POR_ONDA):
		onda = cache_item['onda']
		tarefa = cache_item['tarefa']
		nf_ids = cache_item['nf_ids']
	else:
		onda, nf_ids = _obter_onda_aberta_existente(rota=rota, setor=setor, tipo_embalagem=tipo_embalagem, nf=nf)
		if onda is None:
			onda = OndaSeparacao.objects.create(
				rota=rota,
				setor=setor,
				tipo_embalagem=tipo_embalagem,
				status=OndaSeparacao.Status.PENDENTE,
			)
			nf_ids = set()
			tarefa = Tarefa.objects.create(
				onda=onda,
				nf=None,
				tipo=tipo_tarefa_por_setor(setor),
				setor=setor,
				rota=rota,
				tipo_embalagem=tipo_embalagem,
				status=Tarefa.Status.ABERTO,
			)
		else:
			tarefa = Tarefa.objects.filter(onda=onda).order_by('id').first()
			if tarefa is None:
				tarefa = Tarefa.objects.create(
					onda=onda,
					nf=None,
					tipo=tipo_tarefa_por_setor(setor),
					setor=setor,
					rota=rota,
					tipo_embalagem=tipo_embalagem,
					status=Tarefa.Status.ABERTO,
				)
		if tarefas_lote_cache is not None:
			cache[chave] = {'onda': onda, 'tarefa': tarefa, 'nf_ids': set(nf_ids)}
			nf_ids = cache[chave]['nf_ids']

	if nf.id not in nf_ids:
		onda.nfs.add(nf)
		nf_ids.add(nf.id)
		OndaSeparacao.objects.filter(pk=onda.pk).update(nf_total=len(nf_ids))
		onda.nf_total = len(nf_ids)

	if tarefas_lote_cache is not None:
		cache[chave] = {'onda': onda, 'tarefa': tarefa, 'nf_ids': nf_ids}
	return tarefa, onda


def registrar_item_tarefa_onda(*, tarefa, quantidade):
	quantidade = Decimal(str(quantidade or 0))
	if quantidade <= ZERO_DECIMAL:
		return
	tarefa.itens_total = (tarefa.itens_total or ZERO_DECIMAL) + quantidade
	tarefa.itens_pendentes = (tarefa.itens_pendentes or ZERO_DECIMAL) + quantidade
	tarefa.percentual = ZERO_DECIMAL
	tarefa.save(update_fields=['itens_total', 'itens_pendentes', 'percentual', 'updated_at'])
	if tarefa.onda_id:
		onda = tarefa.onda
		onda.itens_total = (onda.itens_total or ZERO_DECIMAL) + quantidade
		onda.itens_pendentes = (onda.itens_pendentes or ZERO_DECIMAL) + quantidade
		onda.percentual = ZERO_DECIMAL
		onda.save(update_fields=['itens_total', 'itens_pendentes', 'percentual', 'updated_at'])


def atualizar_progresso_bipagem(*, tarefa_id, onda_id=None, operador_id=None, delta=UNIT_DECIMAL, finalizado=False):
	delta = Decimal(str(delta or UNIT_DECIMAL))
	bipado_expr = Coalesce(F('itens_bipados'), Value(ZERO_DECIMAL)) + Value(delta)
	pendente_expr = Greatest(
		Coalesce(F('itens_pendentes'), Value(ZERO_DECIMAL)) - Value(delta),
		Value(ZERO_DECIMAL),
	)
	Tarefa.objects.filter(pk=tarefa_id).update(
		itens_bipados=bipado_expr,
		itens_pendentes=pendente_expr,
		percentual=percentual_expr(total_expr=F('itens_total'), bipado_expr=bipado_expr),
	)
	if onda_id is None:
		return
	OndaSeparacao.objects.filter(pk=onda_id).update(
		operador_id=operador_id,
		itens_bipados=bipado_expr,
		itens_pendentes=pendente_expr,
		percentual=percentual_expr(total_expr=F('itens_total'), bipado_expr=bipado_expr),
		status=OndaSeparacao.Status.AGUARDANDO_CONFERENCIA if finalizado else OndaSeparacao.Status.PARCIAL,
	)


def limpar_referencias_execucao_onda(onda_id):
	if onda_id is None:
		return
	OndaSeparacao.objects.filter(pk=onda_id).update(operador_id=None)