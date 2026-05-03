from django.db import models

from apps.core.models import BaseModel


class Cliente(BaseModel):
	codigo = models.CharField(max_length=50, null=True, blank=True, db_index=True, verbose_name='codigo')
	nome = models.CharField(max_length=255, verbose_name='nome', db_index=True)
	rota = models.CharField(max_length=100, null=True, blank=True, verbose_name='rota', db_index=True)
	inscricao_estadual = models.CharField(
		max_length=50,
		unique=True,
		db_index=True,
		verbose_name='inscricao estadual',
	)
	ativo = models.BooleanField(default=True, verbose_name='ativo')

	class Meta:
		verbose_name = 'cliente'
		verbose_name_plural = 'clientes'
		ordering = ('nome',)
		indexes = [
			models.Index(fields=['nome'], name='cliente_nome_idx'),
			models.Index(fields=['codigo'], name='cliente_codigo_idx'),
			models.Index(fields=['rota'], name='cliente_rota_idx'),
		]

	def __str__(self):
		return self.nome
