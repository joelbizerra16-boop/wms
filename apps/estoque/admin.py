from django.contrib import admin

from apps.estoque.models import EstoqueFisico, MovimentacaoEstoque, PosicaoEstoque


@admin.register(PosicaoEstoque)
class PosicaoEstoqueAdmin(admin.ModelAdmin):
    list_display = (
        'codigo_posicao',
        'rua',
        'posicao',
        'andar',
        'lado',
        'setor',
        'status',
        'ativo',
    )
    list_filter = ('status', 'ativo', 'setor')
    search_fields = ('codigo_posicao', 'rua', 'posicao', 'setor')


@admin.register(EstoqueFisico)
class EstoqueFisicoAdmin(admin.ModelAdmin):
    list_display = (
        'codigo_produto',
        'quantidade',
        'posicao',
        'fifo_nf',
        'data_entrada',
        'nf_entrada',
        'status',
    )
    list_filter = ('status',)
    search_fields = ('codigo_produto', 'fifo_nf', 'nf_entrada', 'descricao')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(MovimentacaoEstoque)
class MovimentacaoEstoqueAdmin(admin.ModelAdmin):
    list_display = (
        'created_at',
        'tipo',
        'codigo_produto',
        'quantidade',
        'fifo_nf',
        'usuario',
        'status',
    )
    list_filter = ('tipo', 'status', 'motivo')
    search_fields = ('codigo_produto', 'fifo_nf', 'nf_entrada', 'descricao')
    readonly_fields = (
        'created_at',
        'updated_at',
        'tipo',
        'codigo_produto',
        'quantidade',
        'fifo_nf',
        'usuario',
    )

    def has_delete_permission(self, request, obj=None):
        return False
