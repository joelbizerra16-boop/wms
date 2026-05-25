"""
Settings de producao (runtime Render / Gunicorn).

Regra: configurar somente o necessario. Nada de validacao de host/porta no import.
Build usa config.settings.build (ver render.yaml).
"""

import dj_database_url
from decouple import Csv, config

from .base import *


DEBUG = False
SECRET_KEY = config('SECRET_KEY')
ALLOWED_HOSTS = config(
	'ALLOWED_HOSTS',
	default='.onrender.com,127.0.0.1,localhost',
	cast=Csv(),
)

DATABASE_URL = config('DATABASE_URL', default='').strip()
if not DATABASE_URL:
	raise RuntimeError('DATABASE_URL obrigatoria em producao.')

DATABASES = {
	'default': dj_database_url.config(
		default=DATABASE_URL,
		conn_max_age=config('DB_CONN_MAX_AGE', default=600, cast=int),
		ssl_require=DATABASE_URL.startswith(('postgres://', 'postgresql://')),
	),
}

_engine = (DATABASES['default'].get('ENGINE') or '').lower()
if 'sqlite' in _engine:
	raise RuntimeError('Producao exige PostgreSQL; SQLite nao e permitido.')
if 'postgresql' in _engine:
	DATABASES['default'].setdefault('OPTIONS', {})
	DATABASES['default']['OPTIONS'].setdefault('sslmode', 'require')
	DATABASES['default']['CONN_HEALTH_CHECKS'] = True
	# Pooler Supabase/PgBouncer: evita "no results to fetch" em COUNT/paginação.
	DATABASES['default']['DISABLE_SERVER_SIDE_CURSORS'] = True

_csrf_origins = config(
	'CSRF_TRUSTED_ORIGINS',
	default='https://wms-okv1.onrender.com,https://.onrender.com',
	cast=Csv(),
)
CSRF_TRUSTED_ORIGINS = list(dict.fromkeys(_csrf_origins))

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

USE_S3_MEDIA_STORAGE = config('USE_S3_MEDIA_STORAGE', default=False, cast=bool)
if USE_S3_MEDIA_STORAGE:
	AWS_STORAGE_BUCKET_NAME = config('AWS_STORAGE_BUCKET_NAME')
	AWS_S3_REGION_NAME = config('AWS_S3_REGION_NAME', default='us-east-1')
	AWS_S3_ENDPOINT_URL = config('AWS_S3_ENDPOINT_URL')
	AWS_S3_ADDRESSING_STYLE = config('AWS_S3_ADDRESSING_STYLE', default='path')
	AWS_S3_SIGNATURE_VERSION = config('AWS_S3_SIGNATURE_VERSION', default='s3v4')
	AWS_ACCESS_KEY_ID = config('AWS_ACCESS_KEY_ID')
	AWS_SECRET_ACCESS_KEY = config('AWS_SECRET_ACCESS_KEY')
	AWS_DEFAULT_ACL = None
	AWS_QUERYSTRING_AUTH = config('AWS_QUERYSTRING_AUTH', default=True, cast=bool)
	AWS_S3_FILE_OVERWRITE = False
	AWS_LOCATION = config('AWS_LOCATION', default='media')
	STORAGES['default'] = {
		'BACKEND': 'apps.core.storage_backends.MediaS3Storage',
	}
	DEFAULT_FILE_STORAGE = 'apps.core.storage_backends.MediaS3Storage'

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
USE_X_FORWARDED_HOST = True
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
		'level': 'ERROR',
	},
	'loggers': {
		'django': {
			'handlers': ['console'],
			'level': 'ERROR',
			'propagate': False,
		},
		'apps.core': {
			'handlers': ['console'],
			'level': LOG_LEVEL,
			'propagate': False,
		},
		'apps.tarefas': {
			'handlers': ['console'],
			'level': LOG_LEVEL,
			'propagate': False,
		},
		'apps.conferencia': {
			'handlers': ['console'],
			'level': LOG_LEVEL,
			'propagate': False,
		},
		'apps.nf': {
			'handlers': ['console'],
			'level': LOG_LEVEL,
			'propagate': False,
		},
		'apps.usuarios': {
			'handlers': ['console'],
			'level': LOG_LEVEL,
			'propagate': False,
		},
		'django.security.csrf': {
			'handlers': ['console'],
			'level': 'WARNING',
			'propagate': False,
		},
	},
}
