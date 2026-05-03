from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.sessions.models import Session
from django.db import models, transaction
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from rest_framework import viewsets

from apps.conferencia.models import Conferencia
from apps.logs.models import UserActivityLog
from apps.tarefas.models import Tarefa
from apps.usuarios.access import build_access_context
from apps.usuarios.models import Usuario, UsuarioSessao
from apps.usuarios.access import get_post_login_redirect_url
from apps.usuarios.serializers import UsuarioSerializer


def login_view(request):
    erro = None
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            UserActivityLog.objects.create(usuario=user, tipo=UserActivityLog.Tipo.LOGIN, timestamp=timezone.now())
            return redirect(get_post_login_redirect_url(user))

        erro = 'Usuário ou senha inválidos.'
    else:
        form = AuthenticationForm(request)

    return render(request, 'login.html', {'erro': erro, 'form': form})


def logout_view(request):
    if request.user.is_authenticated:
        UserActivityLog.objects.create(usuario=request.user, tipo=UserActivityLog.Tipo.LOGOUT, timestamp=timezone.now())
    logout(request)
    return redirect('login')


class UsuarioViewSet(viewsets.ModelViewSet):
    serializer_class = UsuarioSerializer
    queryset = Usuario.objects.all().order_by('nome')
    filterset_fields = ('perfil', 'setores__nome', 'is_active', 'is_staff')
    search_fields = ('nome', 'username')
    ordering_fields = ('nome', 'username', 'created_at', 'updated_at')


def _pode_monitorar_usuarios(user):
    return bool(
        user.is_authenticated
        and (
            user.is_superuser
            or getattr(user, 'perfil', None) == Usuario.Perfil.GESTOR
            or user.groups.filter(name='GESTOR').exists()
        )
    )


@login_required
@user_passes_test(_pode_monitorar_usuarios)
@never_cache
def usuarios_logados(request):
    sessoes_db = list(
        UsuarioSessao.objects.select_related('usuario')
        .filter(usuario__is_active=True)
        .order_by('usuario_id', '-ultimo_acesso')
    )
    sessao_por_usuario = {}
    for sessao in sessoes_db:
        if sessao.usuario_id not in sessao_por_usuario:
            sessao_por_usuario[sessao.usuario_id] = sessao

    usuarios_ativos = Usuario.objects.filter(is_active=True).order_by('username')
    sessoes = []
    tarefas_em_execucao = {
        row['usuario_em_execucao']: row['total']
        for row in Tarefa.objects.filter(
            status=Tarefa.Status.EM_EXECUCAO,
            usuario_em_execucao__isnull=False,
        )
        .values('usuario_em_execucao')
        .annotate(total=models.Count('id'))
    }
    conferencias_em_execucao = {
        row['conferente']: row['total']
        for row in Conferencia.objects.filter(status=Conferencia.Status.EM_CONFERENCIA)
        .values('conferente')
        .annotate(total=models.Count('id'))
    }
    agora = timezone.now()
    for usuario in usuarios_ativos:
        sessao = sessao_por_usuario.get(usuario.id)
        if sessao is None:
            sessao = UsuarioSessao(usuario=usuario, ativo=False, total_logins_dia=0)
            sessao.ultimo_acesso = usuario.last_activity
        referencia_atividade = usuario.last_activity or sessao.ultimo_acesso
        sessao.online = bool(referencia_atividade and (agora - referencia_atividade).total_seconds() < 300)
        sessao.ultimo_atividade = referencia_atividade
        sessao.tarefas_execucao = tarefas_em_execucao.get(usuario.id, 0)
        sessao.conferencias_execucao = conferencias_em_execucao.get(usuario.id, 0)
        sessoes.append(sessao)
    sessoes = sorted(sessoes, key=lambda s: (not s.online, s.usuario.username.lower()))
    context = {'sessoes': sessoes, 'usuario': request.user}
    context.update(build_access_context(request.user))
    return render(request, 'usuarios/logados.html', context)


@login_required
@user_passes_test(_pode_monitorar_usuarios)
def forcar_logout_usuario(request, usuario_id):
    if request.method != 'POST':
        return redirect('usuarios_logados')
    if not (request.user.is_superuser or request.user.is_staff):
        messages.error(request, 'Apenas admin/gestor autorizado pode forçar logout.')
        return redirect('usuarios_logados')
    alvo = Usuario.objects.filter(id=usuario_id).first()
    if not alvo:
        messages.error(request, 'Usuário não encontrado para logout forçado.')
        return redirect('usuarios_logados')
    with transaction.atomic():
        Tarefa.objects.filter(usuario_em_execucao=alvo, status=Tarefa.Status.EM_EXECUCAO).update(
            status=Tarefa.Status.ABERTO,
            usuario=None,
            usuario_em_execucao=None,
            data_inicio=None,
        )
        Tarefa.objects.filter(usuario=alvo, status=Tarefa.Status.EM_EXECUCAO).update(
            status=Tarefa.Status.ABERTO,
            usuario=None,
            usuario_em_execucao=None,
            data_inicio=None,
        )
        Conferencia.objects.filter(conferente=alvo, status=Conferencia.Status.EM_CONFERENCIA).update(
            status=Conferencia.Status.AGUARDANDO,
        )
        for sessao in Session.objects.filter(expire_date__gte=timezone.now()):
            dados = sessao.get_decoded()
            if dados.get('_auth_user_id') == str(alvo.id):
                sessao.delete()
        UsuarioSessao.objects.filter(usuario=alvo).update(ativo=False)
    messages.success(request, f'Usuário {alvo.username} deslogado e tarefas liberadas.')
    return redirect('usuarios_logados')
