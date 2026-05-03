from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import BaseModel


class Rota(BaseModel):
	nome = models.CharField(max_length=100, verbose_name='nome', db_index=True)
	praca = models.CharField(max_length=100, null=True, blank=True, verbose_name='praca', db_index=True)
	nome_rota = models.CharField(max_length=100, null=True, blank=True, verbose_name='nome da rota', db_index=True)
	cep_inicial = models.CharField(max_length=9, null=True, blank=True, verbose_name='CEP inicial')
	cep_final = models.CharField(max_length=9, null=True, blank=True, verbose_name='CEP final')
	cep_inicial_num = models.IntegerField(null=True, blank=True, verbose_name='CEP inicial numerico', db_index=True)
	cep_final_num = models.IntegerField(null=True, blank=True, verbose_name='CEP final numerico', db_index=True)
	bairro = models.CharField(max_length=100, null=True, blank=True, verbose_name='bairro', db_index=True)

	class Meta:
		verbose_name = 'rota'
		verbose_name_plural = 'rotas'
		ordering = ('nome',)
		indexes = [
			models.Index(fields=['nome'], name='rota_nome_idx'),
			models.Index(fields=['praca'], name='rota_praca_idx'),
			models.Index(fields=['nome_rota'], name='rota_nome_rota_idx'),
			models.Index(fields=['bairro'], name='rota_bairro_idx'),
			models.Index(fields=['cep_inicial', 'cep_final'], name='rota_cep_faixa_idx'),
			models.Index(fields=['cep_inicial_num', 'cep_final_num'], name='rota_cep_faixa_num_idx'),
		]
		constraints = [
			models.CheckConstraint(
				condition=(
					(
						models.Q(cep_inicial__isnull=False)
						& ~models.Q(cep_inicial='')
						& models.Q(cep_final__isnull=False)
						& ~models.Q(cep_final='')
						& models.Q(bairro__isnull=True)
					)
					| (
						models.Q(cep_inicial__isnull=True)
						& models.Q(cep_final__isnull=True)
						& models.Q(bairro__isnull=False)
						& ~models.Q(bairro='')
					)
				),
				name='rota_cep_ou_bairro_chk',
			),
		]

	def clean(self):
		tem_faixa_cep = bool(self.cep_inicial and self.cep_final)
		tem_bairro = bool(self.bairro)

		if tem_faixa_cep and tem_bairro:
			raise ValidationError('Informe faixa de CEP ou bairro, nao ambos.')
		if not tem_faixa_cep and not tem_bairro:
			raise ValidationError('Informe faixa de CEP ou bairro.')
		if bool(self.cep_inicial) != bool(self.cep_final):
			raise ValidationError('CEP inicial e CEP final devem ser informados juntos.')

	def __str__(self):
		return self.nome_rota or self.nome
