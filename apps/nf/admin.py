from django.contrib import admin

from apps.nf.models import EntradaNF, NotaFiscal, NotaFiscalItem


class NotaFiscalItemInline(admin.TabularInline):
	model = NotaFiscalItem
	extra = 0
	autocomplete_fields = ('produto',)


@admin.register(NotaFiscal)
class NotaFiscalAdmin(admin.ModelAdmin):
	list_display = ('numero', 'chave_nfe', 'cliente', 'rota', 'status', 'status_fiscal', 'bloqueada', 'ativa', 'data_emissao')
	list_filter = ('status', 'status_fiscal', 'bloqueada', 'ativa', 'rota')
	search_fields = ('numero', 'chave_nfe', 'cliente__nome')
	ordering = ('-data_emissao',)
	readonly_fields = ('created_at', 'updated_at')
	autocomplete_fields = ('cliente', 'rota')
	inlines = (NotaFiscalItemInline,)


@admin.register(NotaFiscalItem)
class NotaFiscalItemAdmin(admin.ModelAdmin):
	list_display = ('nf', 'produto', 'quantidade', 'created_at')
	search_fields = ('nf__numero', 'produto__cod_prod', 'produto__descricao')
	autocomplete_fields = ('nf', 'produto')
	readonly_fields = ('created_at', 'updated_at')


@admin.register(EntradaNF)
class EntradaNFAdmin(admin.ModelAdmin):
	list_display = ('numero_nf', 'chave_nf', 'tipo', 'status', 'data_importacao')
	list_filter = ('tipo', 'status')
	search_fields = ('chave_nf', 'numero_nf')
	readonly_fields = ('data_importacao', 'created_at', 'updated_at')
