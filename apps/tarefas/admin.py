from django.contrib import admin

from apps.tarefas.models import Tarefa, TarefaItem


class TarefaItemInline(admin.TabularInline):
	model = TarefaItem
	extra = 0
	autocomplete_fields = ('produto',)


@admin.register(Tarefa)
class TarefaAdmin(admin.ModelAdmin):
	list_display = ('id', 'nf', 'tipo', 'rota', 'status', 'created_at')
	list_filter = ('tipo', 'status', 'rota')
	search_fields = ('id', 'nf__numero', 'rota__nome')
	autocomplete_fields = ('nf', 'rota')
	readonly_fields = ('created_at', 'updated_at')
	inlines = (TarefaItemInline,)


@admin.register(TarefaItem)
class TarefaItemAdmin(admin.ModelAdmin):
	list_display = ('tarefa', 'produto', 'quantidade_total', 'quantidade_separada', 'created_at')
	search_fields = ('tarefa__id', 'produto__cod_prod', 'produto__descricao')
	autocomplete_fields = ('tarefa', 'produto')
	readonly_fields = ('created_at', 'updated_at')
