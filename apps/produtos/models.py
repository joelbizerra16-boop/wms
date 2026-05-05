from django.db import models

from apps.core.models import BaseModel


class GrupoAgregado(BaseModel):
	nome = models.CharField(max_length=50, unique=True, verbose_name='nome')

	class Meta:
		verbose_name = 'grupo agregado'
		verbose_name_plural = 'grupos agregados'
		ordering = ('nome',)
		indexes = [
			models.Index(fields=['nome'], name='grupo_agregado_nome_idx'),
		]

	def __str__(self):
		return self.nome


class Produto(BaseModel):
	class Categoria(models.TextChoices):
		LUBRIFICANTE = 'LUBRIFICANTE', 'Lubrificante'
		AGREGADO = 'AGREGADO', 'Agregado'
		FILTROS = 'FILTROS', 'Filtros'
		NAO_ENCONTRADO = 'NAO_ENCONTRADO', 'Nao encontrado'

	cod_prod = models.CharField(max_length=50, unique=True, verbose_name='codigo do produto')
	codigo = models.CharField(max_length=50, blank=True, null=True, db_index=True, verbose_name='codigo')
	descricao = models.CharField(max_length=255, verbose_name='descricao', db_index=True)
	embalagem = models.CharField(max_length=20, blank=True, null=True, verbose_name='embalagem')
	cod_ean = models.CharField(max_length=50, db_index=True, blank=True, null=True, verbose_name='codigo EAN')
	setor = models.CharField(max_length=50, blank=True, null=True, db_index=True, verbose_name='setor')
	unidade = models.CharField(max_length=20, null=True, blank=True, verbose_name='unidade')
	categoria = models.CharField(max_length=20, choices=Categoria.choices, verbose_name='categoria')
	grupos_agregados = models.ManyToManyField(
		GrupoAgregado,
		related_name='produtos',
		blank=True,
		verbose_name='grupos agregados',
	)
	ativo = models.BooleanField(default=True, verbose_name='ativo')
	cadastrado_manual = models.BooleanField(default=False, verbose_name='cadastrado manual')
	incompleto = models.BooleanField(default=True, verbose_name='incompleto')

	class Meta:
		verbose_name = 'produto'
		verbose_name_plural = 'produtos'
		ordering = ('cod_prod',)
		indexes = [
			models.Index(fields=['cod_prod'], name='produto_cod_prod_idx'),
			models.Index(fields=['cod_ean'], name='produto_cod_ean_idx'),
			models.Index(fields=['categoria'], name='produto_categoria_idx'),
		]

	def __str__(self):
		codigo_exibicao = self.codigo or self.cod_prod
		return f'{codigo_exibicao} - {self.descricao}'
