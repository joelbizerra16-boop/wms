#!/usr/bin/env bash
# Start Render — reconcile rapido + Gunicorn.
set -euo pipefail
cd "$(dirname "$0")/.."
export DJANGO_SETTINGS_MODULE=config.settings.prod
python manage.py reconcile_minuta_schema
exec gunicorn config.wsgi:application \
  --worker-class gthread \
  --workers "${WEB_CONCURRENCY:-4}" \
  --threads "${GUNICORN_THREADS:-8}" \
  --timeout "${GUNICORN_TIMEOUT:-120}" \
  --bind "0.0.0.0:${PORT}"
