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
DATABASE_URL = config('DATABASE_URL')
CSRF_TRUSTED_ORIGINS = config(
	'CSRF_TRUSTED_ORIGINS',
	default='https://*.onrender.com',
	cast=Csv(),
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

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = config('SECURE_SSL_REDIRECT', default=True, cast=bool)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = config('SECURE_HSTS_SECONDS', default=3600, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True