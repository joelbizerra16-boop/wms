from django.contrib import admin

from apps.clientes.models import Cliente


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
	list_display = ('nome', 'inscricao_estadual', 'created_at', 'updated_at')
	search_fields = ('nome', 'inscricao_estadual')
	ordering = ('nome',)
	readonly_fields = ('created_at', 'updated_at')
