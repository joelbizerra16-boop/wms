from django.conf import settings
from django.db import models

from apps.core.models import BaseModel
from apps.nf.models import NotaFiscal
from apps.produtos.models import Produto


class Conferencia(BaseModel):
	class Status(models.TextChoices):
		AGUARDANDO = 'AGUARDANDO', 'Aguardando'
		EM_CONFERENCIA = 'EM_CONFERENCIA', 'Em conferencia'
		OK = 'OK', 'OK'
		DIVERGENCIA = 'DIVERGENCIA', 'Divergencia'
		LIBERADO_COM_RESTRICAO = 'LIBERADO_COM_RESTRICAO', 'Liberado com restricao'
		CONCLUIDO_COM_RESTRICAO = 'CONCLUIDO_COM_RESTRICAO', 'Concluido com restricao'
		CANCELADA = 'CANCELADA', 'Cancelada'

	nf = models.ForeignKey(NotaFiscal, on_delete=models.CASCADE, related_name='conferencias', verbose_name='nota fiscal')
	conferente = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.PROTECT,
		related_name='conferencias',
		verbose_name='conferente',
	)
	status = models.CharField(max_length=30, choices=Status.choices, verbose_name='status', db_index=True)

	class Meta:
		verbose_name = 'conferencia'
		verbose_name_plural = 'conferencias'
		ordering = ('-created_at',)
		indexes = [
			models.Index(fields=['nf', 'status'], name='conf_nf_status_idx'),
			models.Index(fields=['conferente', 'status'], name='conf_user_status_idx'),
			models.Index(fields=['status', 'updated_at'], name='conf_status_updated_idx'),
		]

	def __str__(self):
		return f'Conferencia {self.id} - {self.nf}'


class ConferenciaItem(BaseModel):
	class Status(models.TextChoices):
		AGUARDANDO = 'AGUARDANDO', 'Aguardando'
		OK = 'OK', 'OK'
		DIVERGENCIA = 'DIVERGENCIA', 'Divergencia'
		CANCELADA = 'CANCELADA', 'Cancelada'

	class MotivoDivergencia(models.TextChoices):
		FALTA = 'FALTA', 'Falta'
		EXCESSO = 'EXCESSO', 'Excesso'
		PRODUTO_ERRADO = 'PRODUTO_ERRADO', 'Produto errado'
		AVARIA = 'AVARIA', 'Avaria'
		OUTRO = 'OUTRO', 'Outro'

	conferencia = models.ForeignKey(
		Conferencia,
		on_delete=models.CASCADE,
		related_name='itens',
		verbose_name='conferencia',
	)
	produto = models.ForeignKey(
		Produto,
		on_delete=models.PROTECT,
		related_name='itens_conferencia',
		verbose_name='produto',
	)
	qtd_esperada = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='quantidade esperada')
	qtd_conferida = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='quantidade conferida')
	status = models.CharField(max_length=20, choices=Status.choices, verbose_name='status', db_index=True)
	motivo_divergencia = models.CharField(
		max_length=20,
		choices=MotivoDivergencia.choices,
		null=True,
		blank=True,
		verbose_name='motivo da divergencia',
	)
	observacao_divergencia = models.TextField(null=True, blank=True, verbose_name='observacao da divergencia')
	bipado_por = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		related_name='itens_bipados_conferencia',
		verbose_name='bipado por',
		null=True,
		blank=True,
	)
	data_bipagem = models.DateTimeField(null=True, blank=True, verbose_name='data da bipagem')

	class Meta:
		verbose_name = 'item da conferencia'
		verbose_name_plural = 'itens da conferencia'
		ordering = ('conferencia_id', 'produto_id')
		constraints = [
			models.UniqueConstraint(fields=['conferencia', 'produto'], name='conf_item_unique_produto'),
			models.CheckConstraint(
				condition=models.Q(qtd_esperada__gte=0) & models.Q(qtd_conferida__gte=0),
				name='conf_item_quantidades_validas_chk',
			),
			models.CheckConstraint(
				condition=(
					~models.Q(status='DIVERGENCIA')
					| (models.Q(motivo_divergencia__isnull=False) & ~models.Q(motivo_divergencia=''))
				),
				name='conf_item_motivo_divergencia_required_chk',
			),
		]
		indexes = [
			models.Index(fields=['conferencia', 'produto'], name='conf_item_conf_prod_idx'),
			models.Index(fields=['status'], name='conf_item_status_idx'),
			models.Index(fields=['motivo_divergencia'], name='conf_item_motivo_idx'),
		]

	def __str__(self):
		return f'{self.conferencia} - {self.produto}'
