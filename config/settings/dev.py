from .base import *

import dj_database_url
from decouple import config


DEBUG = True
ALLOWED_HOSTS = ['127.0.0.1', 'localhost']
CORS_ALLOW_ALL_ORIGINS = True

# Upload em massa de XML (ambiente dev)
DATA_UPLOAD_MAX_NUMBER_FILES = 2000
DATA_UPLOAD_MAX_MEMORY_SIZE = 104857600  # 100MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 104857600  # 100MB

DATABASES = {
	'default': dj_database_url.config(
		default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
		conn_max_age=600,
		ssl_require=config('DEV_DB_SSL_REQUIRE', default=False, cast=bool),
	)
}