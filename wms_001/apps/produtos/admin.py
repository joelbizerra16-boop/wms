from django.contrib import admin

from apps.produtos.models import GrupoAgregado, Produto


@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
	list_display = ('cod_prod', 'codigo', 'descricao', 'embalagem', 'cod_ean', 'setor', 'categoria', 'created_at')
	list_filter = ('categoria', 'setor')
	search_fields = ('cod_prod', 'codigo', 'descricao', 'cod_ean', 'setor')
	ordering = ('cod_prod',)
	readonly_fields = ('created_at', 'updated_at')
	filter_horizontal = ('grupos_agregados',)


@admin.register(GrupoAgregado)
class GrupoAgregadoAdmin(admin.ModelAdmin):
	list_display = ('nome', 'created_at')
	search_fields = ('nome',)
	ordering = ('nome',)
