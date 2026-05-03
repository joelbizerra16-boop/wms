from django.contrib import admin

from apps.logs.models import Log, UserActivityLog


@admin.register(Log)
class LogAdmin(admin.ModelAdmin):
	list_display = ('usuario', 'acao', 'created_at')
	list_filter = ('acao', 'created_at')
	search_fields = ('usuario__nome', 'usuario__username', 'acao', 'detalhe')
	autocomplete_fields = ('usuario',)
	readonly_fields = ('created_at', 'updated_at')


@admin.register(UserActivityLog)
class UserActivityLogAdmin(admin.ModelAdmin):
	list_display = ('usuario', 'tipo', 'tarefa', 'timestamp')
	list_filter = ('tipo', 'timestamp')
	search_fields = ('usuario__nome', 'usuario__username')
	autocomplete_fields = ('usuario', 'tarefa')
	readonly_fields = ('created_at', 'updated_at')
