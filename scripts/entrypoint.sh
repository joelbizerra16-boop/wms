#!/bin/sh
set -e

python manage.py migrate --settings=${DJANGO_SETTINGS_MODULE:-config.settings.prod}
python manage.py collectstatic --noinput --settings=${DJANGO_SETTINGS_MODULE:-config.settings.prod}
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --worker-class gthread --workers 4 --threads 8 --timeout 300 --keep-alive 5