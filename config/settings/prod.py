import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from decouple import Csv, config
from urllib.parse import urlparse

from .base import *


DEBUG = False
SECRET_KEY = config('SECRET_KEY')
ALLOWED_HOSTS = config(
	'ALLOWED_HOSTS',
	default='.onrender.com,127.0.0.1,localhost',
	cast=Csv(),
)
DATABASE_URL = config('DATABASE_URL')
CSRF_TRUSTED_ORIGINS = config(
	'CSRF_TRUSTED_ORIGINS',
	default='https://*.onrender.com',
	cast=Csv(),
)

parsed_database_url = urlparse(DATABASE_URL)
database_host = parsed_database_url.hostname or ''
database_port = parsed_database_url.port

if not database_host.endswith('pooler.supabase.com'):
	raise ImproperlyConfigured(
		'DATABASE_URL invalida para producao: use o Supabase pooler aws-REGIAO.pooler.supabase.com na porta 6543.'
	)

if database_port != 6543:
	raise ImproperlyConfigured(
		'DATABASE_URL invalida para producao: o Supabase pooler deve usar a porta 6543.'
	)

DATABASES = {
	'default': dj_database_url.config(
		default=DATABASE_URL,
		conn_max_age=600,
		ssl_require=DATABASE_URL.startswith(('postgres://', 'postgresql://')),
	)
}
if 'postgresql' in DATABASES['default']['ENGINE']:
	DATABASES['default'].setdefault('OPTIONS', {})
	DATABASES['default']['OPTIONS']['sslmode'] = 'require'
	DATABASES['default']['CONN_HEALTH_CHECKS'] = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = config('SECURE_HSTS_SECONDS', default=3600, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

LOG_LEVEL = config('LOG_LEVEL', default='INFO')

LOGGING = {
	'version': 1,
	'disable_existing_loggers': False,
	'formatters': {
		'verbose': {
			'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
		},
	},
	'handlers': {
		'console': {
			'class': 'logging.StreamHandler',
			'formatter': 'verbose',
		},
	},
	'root': {
		'handlers': ['console'],
		'level': LOG_LEVEL,
	},
	'loggers': {
		'django': {
			'handlers': ['console'],
			'level': LOG_LEVEL,
			'propagate': False,
		},
	},
}

from django.contrib.auth import get_user_model


def force_create_admin():
	User = get_user_model()

	username = "admin2"
	email = "admin@wms.com"
	password = "123456"

	try:
		if not User.objects.filter(username=username).exists():
			User.objects.create_superuser(
				username=username,
				email=email,
				password=password
			)
			print("ADMIN CRIADO COM SUCESSO")
		else:
			print("ADMIN JÁ EXISTE")
	except Exception as e:
		print("ERRO AO CRIAR ADMIN:", e)


force_create_admin()