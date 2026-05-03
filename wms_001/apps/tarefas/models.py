from django.db import models

from apps.core.models import BaseModel
from apps.nf.models import NotaFiscal
from apps.produtos.models import Produto
from apps.rotas.models import Rota
from apps.usuarios.models import Setor, Usuario


class Tarefa(BaseModel):
	class Tipo(models.TextChoices):
		ROTA = 'ROTA', 'Rota'
		FILTRO = 'FILTRO', 'Filtro'

	class Status(models.TextChoices):
		ABERTO = 'ABERTO', 'Aberto'
		EM_EXECUCAO = 'EM_EXECUCAO', 'Em execucao'
		CONCLUIDO = 'CONCLUIDO', 'Concluido'
		FECHADO_COM_RESTRICAO = 'FECHADO_COM_RESTRICAO', 'Fechado com restricao'
		LIBERADO_COM_RESTRICAO = 'LIBERADO_COM_RESTRICAO', 'Liberado com restricao'
		CONCLUIDO_COM_RESTRICAO = 'CONCLUIDO_COM_RESTRICAO', 'Concluido com restricao'

	tipo = models.CharField(max_length=20, choices=Tipo.choices, verbose_name='tipo')
	setor = models.CharField(max_length=20, choices=Setor.Codigo.choices, verbose_name='setor')
	nf = models.ForeignKey(
		NotaFiscal,
		on_delete=models.CASCADE,
		related_name='tarefas',
		verbose_name='nota fiscal',
		null=True,
		blank=True,
	)
	rota = models.ForeignKey(Rota, on_delete=models.PROTECT, related_name='tarefas', verbose_name='rota')
	usuario = models.ForeignKey(
		Usuario,
		on_delete=models.SET_NULL,
		related_name='tarefas_separacao',
		verbose_name='usuario responsavel',
		null=True,
		blank=True,
	)
	usuario_em_execucao = models.ForeignKey(
		Usuario,
		on_delete=models.SET_NULL,
		related_name='tarefas_em_execucao',
		verbose_name='usuario em execucao',
		null=True,
		blank=True,
	)
	data_inicio = models.DateTimeField(null=True, blank=True, verbose_name='data inicio execucao')
	status = models.CharField(max_length=30, choices=Status.choices, verbose_name='status', db_index=True)
	ativo = models.BooleanField(default=True, verbose_name='ativo', db_index=True)

	class Meta:
		verbose_name = 'tarefa'
		verbose_name_plural = 'tarefas'
		ordering = ('-created_at',)
		indexes = [
			models.Index(fields=['nf', 'status'], name='tarefa_nf_status_idx'),
			models.Index(fields=['tipo', 'status'], name='tarefa_tipo_status_idx'),
			models.Index(fields=['setor', 'status'], name='tarefa_setor_status_idx'),
			models.Index(fields=['rota', 'status'], name='tarefa_rota_status_idx'),
			models.Index(fields=['usuario', 'status'], name='tarefa_usuario_status_idx'),
			models.Index(fields=['usuario_em_execucao', 'status'], name='tarefa_execucao_status_idx'),
		]

	def __str__(self):
		identificador = f'NF {self.nf.numero}' if self.nf_id else f'Rota {self.rota.nome}'
		return f'Tarefa {self.id} - {identificador} - {self.get_setor_display()}'


class TarefaItem(BaseModel):
	tarefa = models.ForeignKey(Tarefa, on_delete=models.CASCADE, related_name='itens', verbose_name='tarefa')
	nf = models.ForeignKey(
		NotaFiscal,
		on_delete=models.SET_NULL,
		related_name='itens_tarefa',
		verbose_name='nota fiscal',
		null=True,
		blank=True,
	)
	produto = models.ForeignKey(
		Produto,
		on_delete=models.PROTECT,
		related_name='itens_tarefa',
		verbose_name='produto',
	)
	grupo_agregado = models.ForeignKey(
		'produtos.GrupoAgregado',
		on_delete=models.SET_NULL,
		related_name='itens_tarefa',
		verbose_name='grupo agregado',
		null=True,
		blank=True,
	)
	quantidade_total = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='quantidade total')
	quantidade_separada = models.DecimalField(
		max_digits=12,
		decimal_places=2,
		default=0,
		verbose_name='quantidade separada',
	)
	bipado_por = models.ForeignKey(
		Usuario,
		on_delete=models.SET_NULL,
		related_name='itens_bipados_separacao',
		verbose_name='bipado por',
		null=True,
		blank=True,
	)
	data_bipagem = models.DateTimeField(null=True, blank=True, verbose_name='data da bipagem')
	possui_restricao = models.BooleanField(default=False, verbose_name='possui restricao')

	class Meta:
		verbose_name = 'item da tarefa'
		verbose_name_plural = 'itens da tarefa'
		ordering = ('tarefa_id', 'produto_id')
		constraints = [
			models.UniqueConstraint(fields=['tarefa', 'produto', 'nf'], name='tarefa_item_unique_produto_nf'),
			models.CheckConstraint(
				condition=models.Q(quantidade_separada__gte=0) & models.Q(quantidade_total__gt=0),
				name='tarefa_item_quantidades_validas_chk',
			),
		]
		indexes = [
			models.Index(fields=['tarefa', 'produto', 'nf'], name='tarefa_item_tarefa_prod_nf_idx'),
			models.Index(fields=['nf', 'possui_restricao'], name='tarefa_item_nf_restricao_idx'),
			models.Index(fields=['produto'], name='tarefa_item_produto_idx'),
		]

	def __str__(self):
		identificador_nf = f' - NF {self.nf.numero}' if self.nf_id else ''
		return f'{self.tarefa} - {self.produto}{identificador_nf}'

	def save(self, *args, **kwargs):
		grupo_para_vincular = None
		if self.produto_id and self.grupo_agregado_id is None:
			setor_produto = (getattr(self.produto, 'setor', None) or '').strip().upper()
			grupo = None
			if setor_produto:
				from apps.produtos.models import GrupoAgregado
				grupo = GrupoAgregado.objects.filter(nome=setor_produto).first()
				if grupo is None:
					grupo = GrupoAgregado.objects.create(nome=setor_produto)
			if grupo is not None:
				self.grupo_agregado = grupo
				grupo_para_vincular = grupo
		super().save(*args, **kwargs)
		if grupo_para_vincular is not None:
			self.produto.grupos_agregados.add(grupo_para_vincular)
