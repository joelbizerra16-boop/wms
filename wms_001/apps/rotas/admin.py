from django.contrib import admin

from apps.rotas.models import Rota


@admin.register(Rota)
class RotaAdmin(admin.ModelAdmin):
	list_display = ('nome', 'cep_inicial', 'cep_final', 'bairro', 'created_at')
	search_fields = ('nome', 'bairro', 'cep_inicial', 'cep_final')
	ordering = ('nome',)
	readonly_fields = ('created_at', 'updated_at')
