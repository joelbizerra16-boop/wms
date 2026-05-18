#!/usr/bin/env bash
# Pre-deploy Render — reconcile schema legado + migrations + tarefas leves.
set -euo pipefail
cd "$(dirname "$0")/.."
export DJANGO_SETTINGS_MODULE=config.settings.prod
python manage.py prepare_production_deploy
python manage.py create_render_superuser
python manage.py clearsessions
