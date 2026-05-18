"""
Settings exclusivos para fase de build (collectstatic no Render).

Nao usar em runtime — nao conecta banco, nao valida Supabase/pooler.
"""

from decouple import config

from .base import *


DEBUG = False
SECRET_KEY = config('SECRET_KEY', default='collectstatic-build-only')
ALLOWED_HOSTS = ['*']

DATABASES = {
	'default': {
		'ENGINE': 'django.db.backends.dummy',
	}
}

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
