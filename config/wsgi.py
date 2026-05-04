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


def create_admin_safe():
	try:
		User = get_user_model()
		username = "admin2"
		password = "123456"

		if not User.objects.filter(username=username).exists():
			User.objects.create_superuser(
				username=username,
				email="admin@wms.com",
				password=password
			)
			print("ADMIN CRIADO COM SUCESSO")
		else:
			print("ADMIN JÁ EXISTE")
	except Exception as e:
		print("ERRO AO CRIAR ADMIN:", e)


create_admin_safe()
