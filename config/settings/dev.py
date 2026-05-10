from .base import *

import dj_database_url


DEBUG = True
ALLOWED_HOSTS = ['127.0.0.1', 'localhost']
CORS_ALLOW_ALL_ORIGINS = True

# Upload em massa de XML (ambiente dev)
DATA_UPLOAD_MAX_NUMBER_FILES = 2000
DATA_UPLOAD_MAX_MEMORY_SIZE = 104857600  # 100MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 104857600  # 100MB

DATABASES = {
	'default': dj_database_url.parse(
		'postgresql://postgres:JOELRAFA010103@db.qvsfsccdvuujqxceogfz.supabase.co:5432/postgres?sslmode=require',
		conn_max_age=600,
		ssl_require=True,
	)
}