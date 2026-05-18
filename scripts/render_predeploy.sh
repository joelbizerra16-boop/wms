#!/usr/bin/env bash
# Pre-deploy Render — reconcile schema legado + migrations + tarefas leves.
set -euo pipefail
cd "$(dirname "$0")/.."
export DJANGO_SETTINGS_MODULE=config.settings.prod
python manage.py reconcile_minuta_schema
python manage.py bootstrap_core_migrations
python manage.py migrate --noinput
python manage.py create_render_superuser
python manage.py clearsessions
