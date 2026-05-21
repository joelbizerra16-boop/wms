#!/usr/bin/env bash
# Pre-deploy Render: migrate com --fake-initial para tabelas minuta ja existentes no Supabase.
set -euo pipefail
cd "$(dirname "$0")/.."
python manage.py showmigrations core
python manage.py migrate core --fake-initial --noinput
python manage.py migrate core --noinput
python manage.py migrate --noinput
python manage.py ensure_minuta_brownfield_schema
python manage.py ensure_onda_brownfield_schema
python manage.py create_render_superuser
python manage.py clearsessions
