$env:DJANGO_SETTINGS_MODULE = if ($env:DJANGO_SETTINGS_MODULE) { $env:DJANGO_SETTINGS_MODULE } else { 'config.settings.dev' }
& .\.venv\Scripts\python.exe manage.py makemigrations
& .\.venv\Scripts\python.exe manage.py migrate