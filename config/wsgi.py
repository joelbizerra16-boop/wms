"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.prod')

application = get_wsgi_application()

from django.contrib.auth import get_user_model
from django.db import transaction


def create_admin_safe():
	try:
		User = get_user_model()
		username = "admin2"
		password = "123456"
		has_email_field = any(field.name == 'email' for field in User._meta.fields)

		if not User.objects.filter(username=username).exists():
			with transaction.atomic():
				create_kwargs = {
					'username': username,
					'nome': 'admin2',
					'perfil': 'GESTOR',
					'setor': 'NAO_ENCONTRADO',
					'is_staff': True,
					'is_superuser': True,
					'is_active': True,
				}
				if has_email_field:
					create_kwargs['email'] = 'admin2'

				user = User(**create_kwargs)
				user.set_password(password)
				user.save()
				if hasattr(user, 'definir_setores'):
					user.definir_setores(['NAO_ENCONTRADO'])
			print("ADMIN CRIADO COM SUCESSO")
		else:
			print("ADMIN JÁ EXISTE")
	except Exception as e:
		print("ERRO AO CRIAR ADMIN:", e)


create_admin_safe()
