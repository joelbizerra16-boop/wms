from django.contrib import admin

from apps.conferencia.models import Conferencia, ConferenciaItem


class ConferenciaItemInline(admin.TabularInline):
	model = ConferenciaItem
	extra = 0
	autocomplete_fields = ('produto',)


@admin.register(Conferencia)
class ConferenciaAdmin(admin.ModelAdmin):
	list_display = ('id', 'nf', 'conferente', 'status', 'created_at')
	list_filter = ('status', 'conferente')
	search_fields = ('id', 'nf__numero', 'conferente__nome', 'conferente__username')
	autocomplete_fields = ('nf', 'conferente')
	readonly_fields = ('created_at', 'updated_at')
	inlines = (ConferenciaItemInline,)


@admin.register(ConferenciaItem)
class ConferenciaItemAdmin(admin.ModelAdmin):
	list_display = ('conferencia', 'produto', 'status', 'qtd_esperada', 'qtd_conferida', 'motivo_divergencia')
	list_filter = ('status', 'motivo_divergencia')
	search_fields = ('conferencia__id', 'produto__cod_prod', 'produto__descricao')
	autocomplete_fields = ('conferencia', 'produto')
	readonly_fields = ('created_at', 'updated_at')
