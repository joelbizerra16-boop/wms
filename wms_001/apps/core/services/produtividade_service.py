import json
from datetime import datetime, time, timedelta

from django.db.models import Avg, Case, Count, DurationField, ExpressionWrapper, F, Min, Max, Q, When
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.conferencia.models import Conferencia
from apps.logs.models import UserActivityLog
from apps.usuarios.models import Setor, Usuario


def _parse_date(raw):
    if not raw:
        return None
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError:
        return None


def parse_global_filters(params):
    hoje = timezone.localdate()
    date_from = _parse_date((params.get('date_from') or '').strip()) or hoje
    date_to = _parse_date((params.get('date_to') or '').strip()) or hoje
    if date_to < date_from:
        date_to = date_from
    return {
        'date_from': date_from,
        'date_to': date_to,
        'setor': (params.get('setor') or '').strip(),
        'perfil': (params.get('perfil') or '').strip(),
        'usuario_id': (params.get('usuario_id') or '').strip(),
    }


def _datetime_window(filters):
    start = timezone.make_aware(datetime.combine(filters['date_from'], time.min))
    end = timezone.make_aware(datetime.combine(filters['date_to'], time.max))
    return start, end


def _users_queryset(filters):
    qs = Usuario.objects.filter(perfil__in=[Usuario.Perfil.CONFERENTE, Usuario.Perfil.SEPARADOR])
    if filters['setor']:
        qs = qs.filter(Q(setores__nome=filters['setor']) | Q(setor=filters['setor']))
    if filters['perfil']:
        qs = qs.filter(perfil=filters['perfil'])
    if filters['usuario_id']:
        qs = qs.filter(id=filters['usuario_id'])
    return qs.distinct()


def _setores_usuario(user):
    setores = list(user.setores.values_list('nome', flat=True))
    if not setores and user.setor:
        setores = [user.setor]
    return sorted(set(setores))


def _duration_seconds(duration):
    if not duration:
        return 0.0
    return max(duration.total_seconds(), 0.0)


def _format_duration(duration):
    total_seconds = int(_duration_seconds(duration))
    horas, resto = divmod(total_seconds, 3600)
    minutos, _ = divmod(resto, 60)
    return f'{horas:02d}:{minutos:02d}'


def _task_durations(start_dt, end_dt, user_ids):
    if not user_ids:
        return {}
    eventos = (
        UserActivityLog.objects.filter(
            usuario_id__in=user_ids,
            timestamp__range=(start_dt, end_dt),
            tipo__in=[UserActivityLog.Tipo.TAREFA_INICIO, UserActivityLog.Tipo.TAREFA_FIM],
            tarefa__isnull=False,
        )
        .values('usuario_id', 'tarefa_id')
        .annotate(
            inicio=Min(Case(When(tipo=UserActivityLog.Tipo.TAREFA_INICIO, then='timestamp'))),
            fim=Max(Case(When(tipo=UserActivityLog.Tipo.TAREFA_FIM, then='timestamp'))),
        )
    )
    por_usuario = {}
    for row in eventos:
        if not row['inicio'] or not row['fim']:
            continue
        duracao = row['fim'] - row['inicio']
        if duracao.total_seconds() <= 0:
            continue
        bucket = por_usuario.setdefault(row['usuario_id'], [])
        bucket.append(duracao)
    return por_usuario


def _login_durations(start_dt, end_dt, user_ids):
    if not user_ids:
        return {}
    eventos = (
        UserActivityLog.objects.filter(
            usuario_id__in=user_ids,
            timestamp__range=(start_dt, end_dt),
            tipo__in=[UserActivityLog.Tipo.LOGIN, UserActivityLog.Tipo.LOGOUT],
        )
        .values('usuario_id')
        .annotate(
            primeiro_login=Min(Case(When(tipo=UserActivityLog.Tipo.LOGIN, then='timestamp'))),
            ultimo_logout=Max(Case(When(tipo=UserActivityLog.Tipo.LOGOUT, then='timestamp'))),
        )
    )
    out = {}
    for row in eventos:
        if row['primeiro_login'] and row['ultimo_logout'] and row['ultimo_logout'] >= row['primeiro_login']:
            out[row['usuario_id']] = row['ultimo_logout'] - row['primeiro_login']
    return out


def build_produtividade_data(filters):
    start_dt, end_dt = _datetime_window(filters)
    users_qs = _users_queryset(filters)
    user_ids = list(users_qs.values_list('id', flat=True))

    activity_qs = UserActivityLog.objects.filter(timestamp__range=(start_dt, end_dt), usuario_id__in=user_ids)

    total_bipagens = activity_qs.filter(tipo=UserActivityLog.Tipo.BIPAGEM).count()
    total_tarefas_concluidas = activity_qs.filter(tipo=UserActivityLog.Tipo.TAREFA_FIM).values('tarefa_id').distinct().count()
    usuarios_ativos = activity_qs.values('usuario_id').distinct().count()

    task_durations = _task_durations(start_dt, end_dt, user_ids)
    login_durations = _login_durations(start_dt, end_dt, user_ids)

    avg_task_seconds = []
    for duracoes in task_durations.values():
        for dur in duracoes:
            avg_task_seconds.append(_duration_seconds(dur))
    tempo_medio_tarefa_seg = (sum(avg_task_seconds) / len(avg_task_seconds)) if avg_task_seconds else 0

    conf_qs = Conferencia.objects.filter(
        conferente_id__in=user_ids,
        updated_at__range=(start_dt, end_dt),
        status__in=[Conferencia.Status.OK, Conferencia.Status.DIVERGENCIA, Conferencia.Status.CONCLUIDO_COM_RESTRICAO],
    ).annotate(
        duracao=ExpressionWrapper(F('updated_at') - F('created_at'), output_field=DurationField())
    )
    tempo_medio_conferencia = conf_qs.aggregate(media=Avg('duracao'))['media']

    login_seconds = [_duration_seconds(d) for d in login_durations.values() if d]
    tempo_medio_logado_seg = (sum(login_seconds) / len(login_seconds)) if login_seconds else 0

    bipagens_dia = list(
        activity_qs.filter(tipo=UserActivityLog.Tipo.BIPAGEM)
        .annotate(dia=TruncDate('timestamp'))
        .values('dia')
        .annotate(total=Count('id'))
        .order_by('dia')
    )

    produtividade_usuario = list(
        activity_qs.filter(tipo=UserActivityLog.Tipo.TAREFA_FIM)
        .values('usuario__nome', 'usuario__username')
        .annotate(total=Count('id'))
        .order_by('-total')[:15]
    )

    comparativo_setor_dict = {}
    bipagens_por_usuario = (
        activity_qs.filter(tipo=UserActivityLog.Tipo.BIPAGEM)
        .values('usuario_id')
        .annotate(total=Count('id'))
    )
    users_map = {u.id: u for u in users_qs.prefetch_related('setores')}
    for row in bipagens_por_usuario:
        user = users_map.get(row['usuario_id'])
        if not user:
            continue
        nomes_setor = _setores_usuario(user) or [Setor.Codigo.NAO_ENCONTRADO]
        for setor_nome in nomes_setor:
            comparativo_setor_dict[setor_nome] = comparativo_setor_dict.get(setor_nome, 0) + row['total']
    comparativo_setor = [
        {'setor': setor_nome, 'total': total}
        for setor_nome, total in sorted(comparativo_setor_dict.items(), key=lambda item: item[1], reverse=True)
    ]

    detalhado = []
    for user in users_qs.prefetch_related('setores').order_by('nome'):
        bipagens = activity_qs.filter(usuario=user, tipo=UserActivityLog.Tipo.BIPAGEM).count()
        tarefas = activity_qs.filter(usuario=user, tipo=UserActivityLog.Tipo.TAREFA_FIM).count()
        duracoes_user = task_durations.get(user.id, [])
        media_tarefa = timedelta(seconds=sum(_duration_seconds(d) for d in duracoes_user) / len(duracoes_user)) if duracoes_user else None
        total_logado = login_durations.get(user.id)
        horas_logadas = (_duration_seconds(total_logado) / 3600.0) if total_logado else 0.0
        produtividade = (tarefas / horas_logadas) if horas_logadas > 0 else 0.0
        ultimo_acesso = (
            activity_qs.filter(usuario=user, tipo__in=[UserActivityLog.Tipo.LOGIN, UserActivityLog.Tipo.LOGOUT])
            .order_by('-timestamp')
            .values_list('timestamp', flat=True)
            .first()
        )
        detalhado.append(
            {
                'usuario_id': user.id,
                'usuario': user.nome or user.username,
                'perfil': user.perfil,
                'setores': ', '.join(_setores_usuario(user)),
                'total_bipagens': bipagens,
                'total_tarefas': tarefas,
                'tempo_medio_tarefa': media_tarefa,
                'tempo_total_logado': total_logado,
                'ultimo_acesso': ultimo_acesso,
                'produtividade': produtividade,
            }
        )

    ranking = {
        'top_conferentes': sorted([r for r in detalhado if r['perfil'] == Usuario.Perfil.CONFERENTE], key=lambda x: x['total_bipagens'], reverse=True)[:10],
        'top_separadores': sorted([r for r in detalhado if r['perfil'] == Usuario.Perfil.SEPARADOR], key=lambda x: x['total_tarefas'], reverse=True)[:10],
        'maior_tempo_logado': sorted(detalhado, key=lambda x: _duration_seconds(x['tempo_total_logado']), reverse=True)[:10],
        'melhor_eficiencia': sorted([r for r in detalhado if r['total_tarefas'] > 0], key=lambda x: x['produtividade'], reverse=True)[:10],
    }

    return {
        'cards': {
            'total_bipagens': total_bipagens,
            'total_tarefas_concluidas': total_tarefas_concluidas,
            'tempo_medio_separacao': timedelta(seconds=tempo_medio_tarefa_seg),
            'tempo_medio_conferencia': tempo_medio_conferencia or timedelta(0),
            'usuarios_ativos': usuarios_ativos,
            'tempo_medio_logado': timedelta(seconds=tempo_medio_logado_seg),
        },
        'charts': {
            'bipagens_por_dia': bipagens_dia,
            'produtividade_por_usuario': produtividade_usuario,
            'comparativo_setor': comparativo_setor,
            'tempo_medio_por_tarefa': [
                {'usuario': row['usuario'], 'tempo': (_duration_seconds(row['tempo_medio_tarefa']) / 60.0) if row['tempo_medio_tarefa'] else 0}
                for row in detalhado
            ],
        },
        'detalhado': detalhado,
        'ranking': ranking,
        'filtros': filters,
        'sets': {
            'usuarios': users_qs.order_by('nome'),
            'setores': list(Setor.objects.order_by('nome').values_list('nome', 'nome')) or Setor.Codigo.choices,
            'perfis': [(Usuario.Perfil.CONFERENTE, 'Conferente'), (Usuario.Perfil.SEPARADOR, 'Separador')],
        },
        'helpers': {
            'format_duration': _format_duration,
            'json': json,
        },
    }
