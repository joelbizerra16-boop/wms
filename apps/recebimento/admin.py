from django.contrib import admin

from apps.recebimento.models import EstoqueTemporario


@admin.register(EstoqueTemporario)
class EstoqueTemporarioAdmin(admin.ModelAdmin):
    list_display = (
        'nf_numero',
        'produto_codigo',
        'quantidade',
        'canal',
        'status',
        'data_recebimento',
        'usuario_recebimento',
    )
    list_filter = ('status', 'canal')
    search_fields = ('nf_numero', 'chave_nfe', 'produto_codigo', 'descricao')
    readonly_fields = ('created_at', 'updated_at', 'data_recebimento')
