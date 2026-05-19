from django.contrib import admin

from apps.tarefas.models import OndaSeparacao, Tarefa, TarefaItem


class TarefaItemInline(admin.TabularInline):
	model = TarefaItem
	extra = 0
	autocomplete_fields = ('produto',)


@admin.register(OndaSeparacao)
class OndaSeparacaoAdmin(admin.ModelAdmin):
	list_display = ('codigo', 'rota', 'setor', 'tipo_embalagem', 'status', 'nf_total', 'percentual', 'created_at')
	list_filter = ('status', 'setor', 'tipo_embalagem', 'rota')
	search_fields = ('codigo', 'rota__nome', 'nfs__numero')
	autocomplete_fields = ('rota', 'operador', 'nfs')
	readonly_fields = ('codigo', 'created_at', 'updated_at')


@admin.register(Tarefa)
class TarefaAdmin(admin.ModelAdmin):
	list_display = ('id', 'onda', 'nf', 'tipo', 'rota', 'tipo_embalagem', 'status', 'created_at')
	list_filter = ('tipo', 'status', 'rota', 'tipo_embalagem')
	search_fields = ('id', 'nf__numero', 'rota__nome', 'onda__codigo')
	autocomplete_fields = ('onda', 'nf', 'rota')
	readonly_fields = ('created_at', 'updated_at')
	inlines = (TarefaItemInline,)


@admin.register(TarefaItem)
class TarefaItemAdmin(admin.ModelAdmin):
	list_display = ('tarefa', 'produto', 'quantidade_total', 'quantidade_separada', 'created_at')
	search_fields = ('tarefa__id', 'produto__cod_prod', 'produto__descricao')
	autocomplete_fields = ('tarefa', 'produto')
	readonly_fields = ('created_at', 'updated_at')
