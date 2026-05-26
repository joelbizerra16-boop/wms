from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel


class PosicaoEstoque(BaseModel):
    """Endereço físico do armazém (campos separados; exibição no coletor sem traços)."""

    class Status(models.TextChoices):
        ATIVA = 'ATIVA', 'Ativa'
        BLOQUEADA = 'BLOQUEADA', 'Bloqueada'
        MANUTENCAO = 'MANUTENCAO', 'Manutenção'
        INVENTARIO = 'INVENTARIO', 'Inventário'

    codigo_posicao = models.CharField(max_length=80, unique=True, db_index=True, verbose_name='cód. posição')
    rua = models.CharField(max_length=30, verbose_name='rua')
    posicao = models.CharField(max_length=30, verbose_name='posição')
    andar = models.CharField(max_length=30, verbose_name='andar')
    lado = models.CharField(max_length=30, verbose_name='lado')
    setor = models.CharField(max_length=50, blank=True, default='', db_index=True, verbose_name='setor')
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.ATIVA,
        db_index=True,
        verbose_name='status',
    )
    observacao = models.CharField(max_length=255, blank=True, default='', verbose_name='observação')
    ativo = models.BooleanField(default=True, db_index=True, verbose_name='ativo')

    class Meta:
        verbose_name = 'posição de estoque'
        verbose_name_plural = 'posições de estoque'
        ordering = ('rua', 'posicao', 'andar', 'lado')
        indexes = [
            models.Index(fields=['status', 'ativo'], name='pos_est_status_ativo_ix'),
            models.Index(fields=['rua', 'posicao', 'andar', 'lado'], name='pos_est_endereco_ix'),
        ]

    def __str__(self):
        return self.codigo_posicao

    @property
    def label_coletor(self) -> str:
        """Formato operação: RUA POSIÇÃO ANDAR LADO (espaços, sem traços)."""
        return ' '.join(p for p in (self.rua, self.posicao, self.andar, self.lado) if p)

    def apta_para_separacao(self) -> bool:
        """Andar 1 = pulmão/reserva — não direcionar retirada de separação."""
        try:
            return int(str(self.andar).strip()) >= 2
        except (TypeError, ValueError):
            return True


class EstoqueFisico(BaseModel):
    """Saldo endereçado com FIFO por NF/data — um registro por armazenagem."""

    class Status(models.TextChoices):
        ATIVO = 'ATIVO', 'Ativo'
        BLOQUEADO = 'BLOQUEADO', 'Bloqueado'

    produto = models.ForeignKey(
        'produtos.Produto',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='estoques_fisicos',
        verbose_name='produto',
    )
    codigo_produto = models.CharField(max_length=50, db_index=True, verbose_name='código produto')
    descricao = models.CharField(max_length=255, verbose_name='descrição')
    quantidade = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='quantidade')
    posicao = models.ForeignKey(
        PosicaoEstoque,
        on_delete=models.PROTECT,
        related_name='estoques',
        verbose_name='posição',
    )
    fifo_nf = models.CharField(max_length=32, db_index=True, verbose_name='FIFO')
    data_entrada = models.DateTimeField(db_index=True, verbose_name='data entrada')
    nf_entrada = models.CharField(max_length=20, db_index=True, verbose_name='NF entrada')
    chave_nfe = models.CharField(max_length=44, blank=True, default='', verbose_name='chave NFe')
    estoque_temporario = models.ForeignKey(
        'recebimento.EstoqueTemporario',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='estoques_fisicos',
        verbose_name='origem TEMP',
    )
    usuario_armazenagem = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='armazenagens_realizadas',
        verbose_name='usuário armazenagem',
    )
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.ATIVO,
        db_index=True,
        verbose_name='status',
    )

    class Meta:
        verbose_name = 'estoque físico'
        verbose_name_plural = 'estoques físicos'
        ordering = ('data_entrada', 'nf_entrada', 'codigo_produto')
        indexes = [
            models.Index(fields=['codigo_produto', 'data_entrada'], name='est_fis_prod_data_ix'),
            models.Index(fields=['fifo_nf'], name='est_fis_fifo_ix'),
            models.Index(fields=['posicao', 'status'], name='est_fis_pos_status_ix'),
            models.Index(fields=['status', 'quantidade'], name='est_fis_status_qtd_ix'),
        ]

    def __str__(self):
        return f'{self.codigo_produto} @ {self.posicao_id} ({self.quantidade})'

    @property
    def dias_em_estoque(self) -> int:
        if not self.data_entrada:
            return 0
        delta = timezone.now() - self.data_entrada
        return max(delta.days, 0)


class MovimentacaoEstoque(BaseModel):
    """Histórico imutável de movimentações físicas do estoque."""

    class Tipo(models.TextChoices):
        TRANSFERENCIA = 'TRANSFERENCIA', 'Transferência'
        REABASTECIMENTO = 'REABASTECIMENTO', 'Reabastecimento'
        AJUSTE = 'AJUSTE', 'Ajuste'
        BLOQUEIO = 'BLOQUEIO', 'Bloqueio'
        DESBLOQUEIO = 'DESBLOQUEIO', 'Desbloqueio'
        ARMAZENAGEM = 'ARMAZENAGEM', 'Armazenagem'

    class Motivo(models.TextChoices):
        INVENTARIO = 'INVENTARIO', 'Inventário'
        AVARIA = 'AVARIA', 'Avaria'
        QUEBRA = 'QUEBRA', 'Quebra'
        SOBRA = 'SOBRA', 'Sobra'
        DIVERGENCIA = 'DIVERGENCIA', 'Divergência'
        ERRO_OPERACIONAL = 'ERRO_OPERACIONAL', 'Erro operacional'
        QUARENTENA = 'QUARENTENA', 'Quarentena'
        QUALIDADE = 'QUALIDADE', 'Qualidade'
        RECALL = 'RECALL', 'Recall'
        REABASTECIMENTO = 'REABASTECIMENTO', 'Reabastecimento'
        TRANSFERENCIA = 'TRANSFERENCIA', 'Transferência'
        OUTRO = 'OUTRO', 'Outro'

    class Status(models.TextChoices):
        CONFIRMADO = 'CONFIRMADO', 'Confirmado'

    tipo = models.CharField(max_length=20, choices=Tipo.choices, db_index=True, verbose_name='tipo')
    produto = models.ForeignKey(
        'produtos.Produto',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='movimentacoes_estoque',
        verbose_name='produto',
    )
    codigo_produto = models.CharField(max_length=50, db_index=True, verbose_name='código produto')
    descricao = models.CharField(max_length=255, blank=True, default='', verbose_name='descrição')
    estoque_fisico = models.ForeignKey(
        EstoqueFisico,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='movimentacoes',
        verbose_name='linha estoque',
    )
    posicao_origem = models.ForeignKey(
        PosicaoEstoque,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='movimentacoes_origem',
        verbose_name='posição origem',
    )
    posicao_destino = models.ForeignKey(
        PosicaoEstoque,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='movimentacoes_destino',
        verbose_name='posição destino',
    )
    quantidade = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='quantidade')
    fifo_nf = models.CharField(max_length=32, blank=True, default='', db_index=True, verbose_name='FIFO')
    nf_entrada = models.CharField(max_length=20, blank=True, default='', verbose_name='NF entrada')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='movimentacoes_estoque',
        verbose_name='usuário',
    )
    motivo = models.CharField(max_length=24, blank=True, default='', verbose_name='motivo')
    observacao = models.CharField(max_length=255, blank=True, default='', verbose_name='observação')
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.CONFIRMADO,
        db_index=True,
        verbose_name='status',
    )

    class Meta:
        verbose_name = 'movimentação de estoque'
        verbose_name_plural = 'movimentações de estoque'
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=['codigo_produto', 'created_at'], name='mov_est_prod_data_ix'),
            models.Index(fields=['fifo_nf', 'created_at'], name='mov_est_fifo_data_ix'),
            models.Index(fields=['tipo', 'created_at'], name='mov_est_tipo_data_ix'),
            models.Index(fields=['posicao_origem', 'created_at'], name='mov_est_orig_data_ix'),
            models.Index(fields=['posicao_destino', 'created_at'], name='mov_est_dest_data_ix'),
            models.Index(fields=['status', 'created_at'], name='mov_est_status_data_ix'),
        ]

    def __str__(self):
        return f'{self.tipo} {self.codigo_produto} {self.quantidade}'


class SapVsWmsUpload(BaseModel):
    """Snapshot SAP mais recente — substituído integralmente a cada upload."""

    codigo_produto = models.CharField(max_length=50, db_index=True, verbose_name='código produto')
    descricao = models.CharField(max_length=255, verbose_name='descrição')
    quantidade_sap = models.DecimalField(max_digits=14, decimal_places=2, verbose_name='quantidade SAP')
    setor = models.CharField(max_length=50, blank=True, default='', db_index=True, verbose_name='setor')
    usuario_upload = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='sap_vs_wms_uploads',
        verbose_name='usuário upload',
    )

    class Meta:
        verbose_name = 'upload SAP vs WMS'
        verbose_name_plural = 'uploads SAP vs WMS'
        ordering = ('codigo_produto',)
        indexes = [
            models.Index(fields=['codigo_produto'], name='sap_wms_cod_prod_ix'),
            models.Index(fields=['setor'], name='sap_wms_setor_ix'),
            models.Index(fields=['created_at'], name='sap_wms_created_ix'),
        ]

    def __str__(self):
        return f'{self.codigo_produto} SAP={self.quantidade_sap}'
