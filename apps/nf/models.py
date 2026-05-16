from functools import lru_cache

from django.db import connections, models
from django.db.utils import OperationalError, ProgrammingError

from apps.clientes.models import Cliente
from apps.core.models import BaseModel
from apps.produtos.models import Produto
from apps.rotas.models import Rota


@lru_cache(maxsize=None)
def _nota_fiscal_colunas(alias):
	connection = connections[alias]
	with connection.cursor() as cursor:
		return {
			coluna.name
			for coluna in connection.introspection.get_table_description(cursor, 'nf_notafiscal')
		}


def nota_fiscal_bairro_disponivel(alias='default'):
	try:
		return 'bairro' in _nota_fiscal_colunas(alias)
	except (OperationalError, ProgrammingError):
		return True


def nota_fiscal_bairro_valor(nf):
	if nf is None:
		return ''
	bairro = nf.__dict__.get('bairro', '')
	return (bairro or '').strip()


class NotaFiscalQuerySet(models.QuerySet):
	def with_legacy_bairro_compat(self):
		if nota_fiscal_bairro_disponivel(self.db):
			return self
		return self.defer('bairro')


class NotaFiscalManager(models.Manager.from_queryset(NotaFiscalQuerySet)):
	def get_queryset(self):
		return super().get_queryset().with_legacy_bairro_compat()


class NotaFiscal(BaseModel):
	objects = NotaFiscalManager()

	class Status(models.TextChoices):
		PENDENTE = 'PENDENTE', 'Pendente'
		EM_CONFERENCIA = 'EM_CONFERENCIA', 'Em conferencia'
		CONCLUIDO = 'CONCLUIDO', 'Concluido'
		CONCLUIDO_COM_RESTRICAO = 'CONCLUIDO_COM_RESTRICAO', 'Concluido com restricao'
		NORMAL = 'NORMAL', 'Normal'
		BLOQUEADA_COM_RESTRICAO = 'BLOQUEADA_COM_RESTRICAO', 'Bloqueada com restricao'
		LIBERADA_COM_RESTRICAO = 'LIBERADA_COM_RESTRICAO', 'Liberada com restricao'
		INCONSISTENTE = 'INCONSISTENTE', 'Inconsistente'

	class StatusFiscal(models.TextChoices):
		AUTORIZADA = 'AUTORIZADA', 'Autorizada'
		CANCELADA = 'CANCELADA', 'Cancelada'
		DENEGADA = 'DENEGADA', 'Denegada'

	chave_nfe = models.CharField(max_length=44, unique=True, db_index=True, verbose_name='chave NFe')
	numero = models.CharField(max_length=20, verbose_name='numero', db_index=True)
	cliente = models.ForeignKey(
		Cliente,
		on_delete=models.PROTECT,
		related_name='notas_fiscais',
		verbose_name='cliente',
	)
	rota = models.ForeignKey(
		Rota,
		on_delete=models.PROTECT,
		related_name='notas_fiscais',
		verbose_name='rota',
	)
	status = models.CharField(
		max_length=30,
		choices=Status.choices,
		default=Status.PENDENTE,
		verbose_name='status operacional',
		db_index=True,
	)
	data_emissao = models.DateTimeField(verbose_name='data de emissao', db_index=True)
	bairro = models.CharField(max_length=100, blank=True, default='', db_index=True, verbose_name='bairro da NF')
	status_fiscal = models.CharField(max_length=20, choices=StatusFiscal.choices, verbose_name='status fiscal')
	bloqueada = models.BooleanField(default=False, verbose_name='bloqueada')
	ativa = models.BooleanField(default=True, verbose_name='ativa')
	balcao = models.BooleanField(default=False, verbose_name='pedido balcao', db_index=True)

	class Meta:
		verbose_name = 'nota fiscal'
		verbose_name_plural = 'notas fiscais'
		ordering = ('-data_emissao', '-id')
		indexes = [
			models.Index(fields=['numero'], name='nf_numero_idx'),
			models.Index(fields=['status'], name='nf_status_operacional_idx'),
			models.Index(fields=['status_fiscal', 'ativa'], name='nf_status_ativa_idx'),
			models.Index(fields=['cliente', 'rota'], name='nf_cliente_rota_idx'),
			models.Index(fields=['bairro'], name='nf_bairro_idx'),
		]

	def __str__(self):
		return f'NF {self.numero}'


class NotaFiscalItem(BaseModel):
	nf = models.ForeignKey(
		NotaFiscal,
		on_delete=models.CASCADE,
		related_name='itens',
		verbose_name='nota fiscal',
	)
	produto = models.ForeignKey(
		Produto,
		on_delete=models.PROTECT,
		related_name='itens_nota_fiscal',
		verbose_name='produto',
		null=True,
		blank=True,
	)
	cod_prod_xml = models.CharField(max_length=50, blank=True, default='', verbose_name='codigo do produto no XML')
	descricao_xml = models.CharField(max_length=255, blank=True, default='', verbose_name='descricao do produto no XML')
	cod_ean_xml = models.CharField(max_length=50, blank=True, default='', verbose_name='codigo EAN no XML')
	quantidade = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='quantidade')

	class Meta:
		verbose_name = 'item da nota fiscal'
		verbose_name_plural = 'itens da nota fiscal'
		ordering = ('nf_id', 'produto_id')
		constraints = [
			models.UniqueConstraint(fields=['nf', 'produto'], name='nf_item_unique_produto'),
		]
		indexes = [
			models.Index(fields=['nf', 'produto'], name='nf_item_nf_prod_idx'),
			models.Index(fields=['produto'], name='nf_item_produto_idx'),
		]

	def __str__(self):
		return f'{self.nf} - {self.descricao_operacional}'

	@property
	def codigo_operacional(self):
		if self.produto_id:
			return self.produto.cod_prod
		return self.cod_prod_xml

	@property
	def descricao_operacional(self):
		if self.produto_id:
			return self.produto.descricao
		return self.descricao_xml or self.cod_prod_xml or 'Produto sem cadastro'

	@property
	def ean_operacional(self):
		if self.produto_id:
			return self.produto.cod_ean or ''
		return self.cod_ean_xml or ''


class EntradaNF(BaseModel):
	class Status(models.TextChoices):
		AGUARDANDO = 'AGUARDANDO', 'Aguardando'
		PROCESSADO = 'PROCESSADO', 'Processado'
		LIBERADO = 'LIBERADO', 'Liberado'

	class Tipo(models.TextChoices):
		BALCAO = 'BALCAO', 'Balcao'
		NORMAL = 'NORMAL', 'Normal'

	chave_nf = models.CharField(max_length=44, unique=True, db_index=True, verbose_name='chave NF')
	numero_nf = models.CharField(max_length=20, blank=True, default='', db_index=True, verbose_name='numero NF')
	xml = models.FileField(upload_to='xmls/', verbose_name='arquivo XML')
	xml_backup_gzip = models.BinaryField(null=True, blank=True, editable=False, verbose_name='backup XML compactado')
	status = models.CharField(max_length=20, choices=Status.choices, default=Status.AGUARDANDO, db_index=True)
	tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.NORMAL, db_index=True)
	data_importacao = models.DateTimeField(auto_now_add=True, db_index=True)

	class Meta:
		verbose_name = 'entrada de NF'
		verbose_name_plural = 'entradas de NF'
		ordering = ('-data_importacao', '-id')
		indexes = [
			models.Index(fields=['status', 'data_importacao'], name='entrada_nf_status_data_idx'),
		]

	def __str__(self):
		numero = self.numero_nf or '-'
		return f'Entrada NF {numero} ({self.chave_nf})'
