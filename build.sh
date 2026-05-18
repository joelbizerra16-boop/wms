#!/usr/bin/env bash
# BUILD Render — copie este arquivo como Build Command: bash build.sh
# NAO executa migrate (evita relation already exists no Supabase legado).
set -euxo pipefail
echo "=== WMS BUILD START ==="
pip install -r requirements.txt
export DJANGO_SETTINGS_MODULE=config.settings.build
export SECRET_KEY="${SECRET_KEY:-collectstatic-build-only}"
python manage.py collectstatic --noinput
echo "=== WMS BUILD OK ==="
