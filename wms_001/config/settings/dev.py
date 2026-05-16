from .base import *


DEBUG = True
ALLOWED_HOSTS = ['127.0.0.1', 'localhost']
CORS_ALLOW_ALL_ORIGINS = True

# Upload em massa de XML (ambiente dev)
DATA_UPLOAD_MAX_NUMBER_FILES = 2000
DATA_UPLOAD_MAX_MEMORY_SIZE = 262144000  # 250MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 262144000  # 250MB

DEV_USE_SQLITE = config('DEV_USE_SQLITE', default=True, cast=bool)

if DEV_USE_SQLITE:
	DATABASES = {
		'default': {
			'ENGINE': 'django.db.backends.sqlite3',
			'NAME': BASE_DIR / 'db.sqlite3',
			'OPTIONS': {
				'timeout': 20,
			},
		}
	}