from datetime import timedelta

from django import template

register = template.Library()


@register.filter(name='format_duration')
def format_duration(value):
    if not value:
        return '00:00'

    if isinstance(value, timedelta):
        total_seconds = int(max(value.total_seconds(), 0))
    else:
        try:
            total_seconds = int(max(float(value), 0))
        except (TypeError, ValueError):
            return '00:00'

    horas, resto = divmod(total_seconds, 3600)
    minutos, _ = divmod(resto, 60)
    return f"{horas:02d}:{minutos:02d}"
