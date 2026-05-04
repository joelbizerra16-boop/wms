import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.usuarios.models import Setor


class Command(BaseCommand):
	help = 'Cria ou atualiza automaticamente o superuser inicial para ambientes de deploy.'

	def handle(self, *args, **options):
		username = os.getenv('DJANGO_SUPERUSER_USERNAME')
		password = os.getenv('DJANGO_SUPERUSER_PASSWORD')
		email = os.getenv('DJANGO_SUPERUSER_EMAIL')

		if not username or not password:
			self.stdout.write(self.style.WARNING('Superuser nao criado: defina DJANGO_SUPERUSER_USERNAME e DJANGO_SUPERUSER_PASSWORD.'))
			return

		User = get_user_model()
		has_email_field = any(field.name == 'email' for field in User._meta.fields)

		if User.objects.filter(is_superuser=True).exists():
			self.stdout.write(self.style.SUCCESS('Superuser ja existe. Nenhuma acao necessaria.'))
			return

		defaults = {
			'nome': 'Administrador',
			'perfil': 'GESTOR',
			'setor': Setor.Codigo.NAO_ENCONTRADO,
		}
		if has_email_field and email:
			defaults['email'] = email

		with transaction.atomic():
			user = User.objects.filter(username=username).first()
			if user:
				user.is_staff = True
				user.is_superuser = True
				user.is_active = True
				if hasattr(user, 'nome'):
					user.nome = getattr(user, 'nome', None) or defaults['nome']
				if hasattr(user, 'perfil'):
					user.perfil = defaults['perfil']
				if hasattr(user, 'setor'):
					user.setor = defaults['setor']
				if has_email_field and email:
					user.email = email
				user.set_password(password)
				user.save()
				if hasattr(user, 'definir_setores'):
					user.definir_setores([Setor.Codigo.NAO_ENCONTRADO])
				self.stdout.write(self.style.SUCCESS(f'Superuser {username} atualizado com sucesso.'))
				return

			create_kwargs = defaults.copy()
			create_kwargs['username'] = username
			create_kwargs['password'] = password
			if has_email_field and email:
				create_kwargs['email'] = email

			User.objects.create_superuser(**create_kwargs)
			self.stdout.write(self.style.SUCCESS(f'Superuser {username} criado com sucesso.'))