from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class EstoqueTemporario(BaseModel):
    """Itens recebidos aguardando ativação — não alimenta separação/conferência."""

    class Status(models.TextChoices):
        TEMP = 'TEMP', 'Temporário'
        VALIDADO = 'VALIDADO', 'Validado'
        RESGATADO = 'RESGATADO', 'Resgatado'
        CANCELADO = 'CANCELADO', 'Cancelado'

    class Canal(models.TextChoices):
        TEMP = 'TEMP', 'TEMP'

    chave_nfe = models.CharField(max_length=44, db_index=True, verbose_name='chave NFe')
    nf_numero = models.CharField(max_length=20, db_index=True, verbose_name='número NF')
    produto_codigo = models.CharField(max_length=50, db_index=True, verbose_name='código produto')
    descricao = models.CharField(max_length=255, verbose_name='descrição')
    quantidade = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='quantidade')
    data_recebimento = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='data recebimento')
    usuario_recebimento = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='estoques_temporarios_recebidos',
        verbose_name='usuário recebimento',
    )
    canal = models.CharField(
        max_length=10,
        choices=Canal.choices,
        default=Canal.TEMP,
        db_index=True,
        verbose_name='canal',
    )
    xml_origem = models.CharField(max_length=255, blank=True, default='', verbose_name='origem XML')
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.TEMP,
        db_index=True,
        verbose_name='status',
    )
    tp_nf = models.CharField(max_length=1, blank=True, default='', verbose_name='tpNF')
    nat_op = models.CharField(max_length=120, blank=True, default='', verbose_name='natOp')
    emitente_cnpj = models.CharField(max_length=14, blank=True, default='', verbose_name='CNPJ emitente')
    destinatario_cnpj = models.CharField(max_length=14, blank=True, default='', verbose_name='CNPJ destinatário')

    class Meta:
        verbose_name = 'estoque temporário'
        verbose_name_plural = 'estoques temporários'
        ordering = ('-data_recebimento', 'nf_numero', 'produto_codigo')
        indexes = [
            models.Index(fields=['status', 'data_recebimento'], name='est_temp_status_data_ix'),
            models.Index(fields=['nf_numero', 'produto_codigo'], name='est_temp_nf_prod_ix'),
        ]

    def __str__(self):
        return f'{self.nf_numero} {self.produto_codigo} ({self.quantidade})'
