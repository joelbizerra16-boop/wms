from django.conf import settings
from django.db import models

from apps.core.models import BaseModel
from apps.nf.models import NotaFiscal
from apps.tarefas.models import Tarefa


class Log(BaseModel):
	usuario = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.PROTECT,
		related_name='logs',
		verbose_name='usuario',
	)
	acao = models.CharField(max_length=255, verbose_name='acao', db_index=True)
	detalhe = models.TextField(verbose_name='detalhe')

	class Meta:
		verbose_name = 'log'
		verbose_name_plural = 'logs'
		ordering = ('-created_at',)
		indexes = [
			models.Index(fields=['usuario', 'created_at'], name='log_usuario_created_idx'),
			models.Index(fields=['acao'], name='log_acao_idx'),
		]

	def __str__(self):
		return f'{self.usuario} - {self.acao}'


class LiberacaoDivergencia(BaseModel):
	usuario = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.PROTECT,
		related_name='liberacoes_divergencia',
		verbose_name='usuario',
	)
	nf = models.ForeignKey(
		NotaFiscal,
		on_delete=models.PROTECT,
		related_name='liberacoes_divergencia',
		verbose_name='nota fiscal',
		null=True,
		blank=True,
	)
	tarefa = models.ForeignKey(
		Tarefa,
		on_delete=models.PROTECT,
		related_name='liberacoes_divergencia',
		verbose_name='tarefa',
		null=True,
		blank=True,
	)
	motivo = models.TextField(verbose_name='motivo')
	nf_numero = models.CharField(max_length=20, null=True, blank=True, verbose_name='numero da nf')
	status_anterior = models.CharField(max_length=40, verbose_name='status anterior')
	status_novo = models.CharField(max_length=40, verbose_name='status novo')

	class Meta:
		verbose_name = 'liberacao de divergencia'
		verbose_name_plural = 'liberacoes de divergencia'
		ordering = ('-created_at',)
		constraints = [
			models.CheckConstraint(
				condition=models.Q(nf__isnull=False) | models.Q(tarefa__isnull=False),
				name='lib_div_nf_or_tarefa_required_chk',
			),
		]
		indexes = [
			models.Index(fields=['created_at'], name='lib_div_created_idx'),
			models.Index(fields=['usuario', 'created_at'], name='lib_div_user_created_idx'),
			models.Index(fields=['status_anterior', 'status_novo'], name='lib_div_status_idx'),
		]

	def __str__(self):
		alvo = f'NF {self.nf.numero}' if self.nf_id else f'Tarefa {self.tarefa_id}'
		return f'{self.usuario} - {alvo} - {self.status_novo}'


class UserActivityLog(BaseModel):
	class Tipo(models.TextChoices):
		LOGIN = 'login', 'Login'
		LOGOUT = 'logout', 'Logout'
		BIPAGEM = 'bipagem', 'Bipagem'
		TAREFA_INICIO = 'tarefa_inicio', 'Tarefa inicio'
		TAREFA_FIM = 'tarefa_fim', 'Tarefa fim'

	usuario = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.PROTECT,
		related_name='activity_logs',
		verbose_name='usuario',
	)
	tipo = models.CharField(max_length=20, choices=Tipo.choices, verbose_name='tipo', db_index=True)
	tarefa = models.ForeignKey(
		Tarefa,
		on_delete=models.SET_NULL,
		related_name='activity_logs',
		verbose_name='tarefa',
		null=True,
		blank=True,
	)
	timestamp = models.DateTimeField(verbose_name='timestamp', db_index=True)

	class Meta:
		verbose_name = 'user activity log'
		verbose_name_plural = 'user activity logs'
		ordering = ('-timestamp',)
		indexes = [
			models.Index(fields=['usuario', 'timestamp'], name='ual_user_ts_idx'),
			models.Index(fields=['tipo', 'timestamp'], name='ual_tipo_ts_idx'),
			models.Index(fields=['tarefa', 'timestamp'], name='ual_tarefa_ts_idx'),
		]

	def __str__(self):
		return f'{self.usuario} - {self.tipo} - {self.timestamp}'
