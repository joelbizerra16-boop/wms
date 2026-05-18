#!/usr/bin/env bash
# Build Render — SEM migrate (nao usar banco nesta fase).
set -euo pipefail
cd "$(dirname "$0")/.."
pip install -r requirements.txt
export DJANGO_SETTINGS_MODULE=config.settings.build
python manage.py collectstatic --noinput
