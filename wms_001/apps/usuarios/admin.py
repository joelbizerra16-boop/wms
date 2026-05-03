from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from apps.usuarios.models import Setor, Usuario


@admin.register(Setor)
class SetorAdmin(admin.ModelAdmin):
	list_display = ('nome',)
	search_fields = ('nome',)


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
	model = Usuario
	list_display = ('username', 'nome', 'perfil', 'setores_display', 'is_active', 'is_staff', 'created_at')
	list_filter = ('perfil', 'setores', 'is_active', 'is_staff', 'is_superuser')
	search_fields = ('username', 'nome')
	ordering = ('nome',)
	readonly_fields = ('created_at', 'updated_at', 'last_login')
	filter_horizontal = ('setores', 'groups', 'user_permissions')
	fieldsets = (
		(None, {'fields': ('username', 'password')}),
		('Informacoes pessoais', {'fields': ('nome', 'perfil', 'setores', 'setor')}),
		('Permissoes', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
		('Auditoria', {'fields': ('last_login', 'created_at', 'updated_at')}),
	)
	add_fieldsets = (
		(
			None,
			{
				'classes': ('wide',),
				'fields': ('username', 'nome', 'perfil', 'setores', 'setor', 'password1', 'password2', 'is_active', 'is_staff'),
			},
		),
	)

	def setores_display(self, obj):
		return ', '.join(obj.setores.values_list('nome', flat=True)) or obj.setor

	setores_display.short_description = 'Setores'
