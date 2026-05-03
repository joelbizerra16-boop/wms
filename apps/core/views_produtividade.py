import json

from django.http import HttpResponse
from django.shortcuts import redirect, render

from apps.core.services.produtividade_service import build_produtividade_data, parse_global_filters
from apps.usuarios.access import build_access_context
from apps.usuarios.models import Usuario
from django.contrib.auth.decorators import login_required
from django.contrib import messages


def _render(request, template_name, context=None):
    base_context = {'usuario': request.user}
    base_context.update(build_access_context(request.user))
    if context:
        base_context.update(context)
    return render(request, template_name, base_context)


def _base_context(request):
    filtros = parse_global_filters(request.GET)
    data = build_produtividade_data(filtros)
    return {
        **data,
        'charts_json': json.dumps(data['charts'], default=str),
        'filtros': filtros,
        'usuarios_filtro': data['sets']['usuarios'],
        'setores_filtro': data['sets']['setores'],
        'perfis_filtro': data['sets']['perfis'],
        'format_duration': data['helpers']['format_duration'],
    }


def _require_gestor_or_admin(view):
    @login_required
    def _wrapped(request, *args, **kwargs):
        if request.user.is_superuser or getattr(request.user, 'perfil', None) == Usuario.Perfil.GESTOR:
            return view(request, *args, **kwargs)
        messages.warning(request, 'Acesso permitido apenas para gestão.')
        return redirect('home')
    return _wrapped


@_require_gestor_or_admin
def produtividade_dashboard(request):
    return _render(request, 'produtividade/dashboard.html', _base_context(request))


@_require_gestor_or_admin
def produtividade_relatorio(request):
    return _render(request, 'produtividade/relatorio.html', _base_context(request))


@_require_gestor_or_admin
def produtividade_ranking(request):
    return _render(request, 'produtividade/ranking.html', _base_context(request))


@_require_gestor_or_admin
def produtividade_export_excel(request):
    try:
        from openpyxl import Workbook
    except ModuleNotFoundError:
        messages.error(
            request,
            'Biblioteca openpyxl não instalada. Execute: pip install openpyxl',
        )
        return redirect('web-produtividade-relatorio')

    data = _base_context(request)
    wb = Workbook()
    ws = wb.active
    ws.title = 'Produtividade'
    ws.append(['Usuário', 'Perfil', 'Setores', 'Total Bipagens', 'Total Tarefas', 'Tempo Médio Tarefa', 'Tempo Total Logado', 'Último Acesso', 'Produtividade'])
    for row in data['detalhado']:
        ws.append(
            [
                row['usuario'],
                row['perfil'],
                row['setores'],
                row['total_bipagens'],
                row['total_tarefas'],
                data['format_duration'](row['tempo_medio_tarefa']),
                data['format_duration'](row['tempo_total_logado']),
                row['ultimo_acesso'].strftime('%d/%m/%Y %H:%M') if row['ultimo_acesso'] else '-',
                round(row['produtividade'], 2),
            ]
        )
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="gestao_produtividade.xlsx"'
    wb.save(response)
    return response


@_require_gestor_or_admin
def produtividade_export_pdf(request):
    data = _base_context(request)
    return _render(request, 'produtividade/export_pdf.html', data)
