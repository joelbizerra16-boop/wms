import uuid

from django.conf import settings
from django.db import models


class BaseModel(models.Model):
	created_at = models.DateTimeField(auto_now_add=True, verbose_name='criado em')
	updated_at = models.DateTimeField(auto_now=True, verbose_name='atualizado em')

	class Meta:
		abstract = True
		ordering = ('-created_at',)


class MinutaRomaneio(BaseModel):
	class StatusExpedicao(models.TextChoices):
		ATIVA = 'ATIVA', 'Ativa'
		IMPRESSA = 'IMPRESSA', 'Impressa'

	codigo_romaneio = models.CharField(max_length=40, db_index=True, verbose_name='codigo do romaneio')
	importacao_lote = models.UUIDField(default=uuid.uuid4, db_index=True, editable=False, verbose_name='lote da importacao')
	filial = models.CharField(max_length=255, blank=True, default='', verbose_name='filial')
	data_saida = models.DateField(null=True, blank=True, db_index=True, verbose_name='data de saida')
	destino = models.CharField(max_length=255, blank=True, default='', verbose_name='destino')
	km = models.CharField(max_length=50, blank=True, default='', verbose_name='km')
	rotas = models.CharField(max_length=255, blank=True, default='', verbose_name='rotas')
	quantidade_pedidos = models.PositiveIntegerField(null=True, blank=True, verbose_name='quantidade de pedidos')
	quantidade_clientes = models.PositiveIntegerField(null=True, blank=True, verbose_name='quantidade de clientes')
	veiculo = models.CharField(max_length=255, blank=True, default='', verbose_name='veiculo')
	placa = models.CharField(max_length=30, blank=True, default='', db_index=True, verbose_name='placa')
	motorista = models.CharField(max_length=255, blank=True, default='', verbose_name='motorista')
	ajudante_1 = models.CharField(max_length=255, blank=True, default='', verbose_name='ajudante 1')
	ajudante_2 = models.CharField(max_length=255, blank=True, default='', verbose_name='ajudante 2')
	ajudante_3 = models.CharField(max_length=255, blank=True, default='', verbose_name='ajudante 3')
	numero_box = models.CharField(max_length=50, blank=True, default='', verbose_name='numero do box')
	transportadora = models.CharField(max_length=255, blank=True, default='', verbose_name='transportadora')
	arquivo_nome = models.CharField(max_length=255, blank=True, default='', verbose_name='arquivo importado')
	usuario_importacao = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		related_name='romaneios_minuta_importados',
		null=True,
		blank=True,
		verbose_name='usuario responsavel pela importacao',
	)
	pdf_gerado_em = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name='pdf gerado em')
	pdf_gerado_por = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.SET_NULL,
		related_name='romaneios_minuta_pdf_gerados',
		null=True,
		blank=True,
		verbose_name='usuario que gerou o pdf',
	)
	tipo_minuta = models.CharField(max_length=40, blank=True, default='', verbose_name='tipo da minuta')
	hash_operacional = models.CharField(max_length=64, blank=True, default='', db_index=True, verbose_name='hash operacional')
	status_expedicao = models.CharField(
		max_length=20,
		choices=StatusExpedicao.choices,
		default=StatusExpedicao.ATIVA,
		db_index=True,
		verbose_name='status da expedicao',
	)

	class Meta:
		verbose_name = 'romaneio da minuta'
		verbose_name_plural = 'romaneios da minuta'
		ordering = ('-data_saida', '-id')
		constraints = [
			models.UniqueConstraint(fields=['codigo_romaneio', 'data_saida'], name='min_rom_cod_data_uq'),
		]
		indexes = [
			models.Index(fields=['codigo_romaneio', 'data_saida'], name='min_rom_cod_data_ix'),
			models.Index(fields=['created_at'], name='min_rom_created_ix'),
			models.Index(fields=['status_expedicao', 'pdf_gerado_em'], name='min_rom_exp_pdf_ix'),
		]

	def __str__(self):
		return f'Romaneio {self.codigo_romaneio}'


class MinutaRomaneioItem(BaseModel):
	romaneio = models.ForeignKey(
		MinutaRomaneio,
		on_delete=models.CASCADE,
		related_name='itens',
		verbose_name='romaneio',
	)
	nf = models.ForeignKey(
		'nf.NotaFiscal',
		on_delete=models.SET_NULL,
		related_name='itens_minuta_romaneio',
		null=True,
		blank=True,
		verbose_name='nota fiscal vinculada',
	)
	numero_nota = models.CharField(max_length=20, db_index=True, verbose_name='numero da nota')
	sequencia_entrega = models.CharField(max_length=20, blank=True, default='', verbose_name='sequencia da entrega')
	codigo_cliente = models.CharField(max_length=50, blank=True, default='', verbose_name='codigo do cliente')
	fantasia = models.CharField(max_length=255, blank=True, default='', verbose_name='fantasia')
	razao_social = models.CharField(max_length=255, blank=True, default='', verbose_name='razao social')
	bairro = models.CharField(max_length=100, blank=True, default='', verbose_name='bairro')
	numero_pedido = models.CharField(max_length=50, blank=True, default='', verbose_name='numero do pedido')
	tipo_cobranca = models.CharField(max_length=100, blank=True, default='', verbose_name='tipo de cobranca')
	peso_kg = models.DecimalField(max_digits=14, decimal_places=3, default=0, verbose_name='peso em kg')
	volume_m3 = models.DecimalField(max_digits=14, decimal_places=3, default=0, verbose_name='volume em m3')
	valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0, verbose_name='valor total')
	status = models.CharField(max_length=40, blank=True, default='PENDENTE', db_index=True, verbose_name='status')
	duplicado = models.BooleanField(default=False, db_index=True, verbose_name='duplicado')
	duplicidade_romaneio_codigo = models.CharField(max_length=40, blank=True, default='', verbose_name='romaneio anterior')
	duplicidade_data_saida = models.DateField(null=True, blank=True, verbose_name='data do romaneio anterior')
	duplicidade_placa = models.CharField(max_length=30, blank=True, default='', verbose_name='placa anterior')
	duplicidade_motorista = models.CharField(max_length=255, blank=True, default='', verbose_name='motorista anterior')
	duplicidade_usuario = models.CharField(max_length=150, blank=True, default='', verbose_name='usuario anterior')

	class Meta:
		verbose_name = 'item da minuta'
		verbose_name_plural = 'itens da minuta'
		ordering = ('-romaneio__data_saida', 'romaneio__codigo_romaneio', 'numero_nota')
		constraints = [
			models.UniqueConstraint(fields=['romaneio', 'numero_nota'], name='min_item_rom_nf_uq'),
		]
		indexes = [
			models.Index(fields=['numero_nota'], name='min_item_nota_ix'),
			models.Index(fields=['duplicado', 'status'], name='min_item_dup_st_ix'),
		]

	def __str__(self):
		return f'{self.romaneio} - NF {self.numero_nota}'
