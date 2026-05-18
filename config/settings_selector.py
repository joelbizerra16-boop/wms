import os


PRODUCTION_ENV_VALUES = {'prod', 'production'}
RENDER_MARKERS = (
    'RENDER',
    'RENDER_EXTERNAL_URL',
    'RENDER_SERVICE_ID',
    'RENDER_INSTANCE_ID',
)


def is_production_environment(environ=None):
    environ = environ or os.environ
    environment = (environ.get('ENVIRONMENT') or environ.get('APP_ENV') or '').strip().lower()
    if environment in PRODUCTION_ENV_VALUES:
        return True
    if environ.get('DATABASE_URL'):
        return True
    return any(environ.get(marker) for marker in RENDER_MARKERS)


def default_settings_module(environ=None):
    return 'config.settings.prod' if is_production_environment(environ) else 'config.settings.dev'