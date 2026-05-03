from datetime import timedelta

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel


class Setor(models.Model):
	class Codigo(models.TextChoices):
		LUBRIFICANTE = 'LUBRIFICANTE', 'Lubrificante'
		AGREGADO = 'AGREGADO', 'Agregado'
		FILTROS = 'FILTROS', 'Filtros'
		NAO_ENCONTRADO = 'NAO_ENCONTRADO', 'Nao encontrado'

	nome = models.CharField(max_length=100, unique=True)

	class Meta:
		verbose_name = 'setor'
		verbose_name_plural = 'setores'
		ordering = ('nome',)

	def __str__(self):
		return self.nome

	@classmethod
	def nomes_padrao(cls):
		return [
			cls.Codigo.LUBRIFICANTE,
			cls.Codigo.FILTROS,
			cls.Codigo.AGREGADO,
			cls.Codigo.NAO_ENCONTRADO,
		]

	@classmethod
	def garantir_setores_padrao(cls):
		for nome in cls.nomes_padrao():
			cls.objects.get_or_create(nome=nome)


SETOR_CHOICES = Setor.Codigo.choices


class UsuarioManager(BaseUserManager):
	def create_user(self, username, nome, perfil, setor=None, password=None, **extra_fields):
		if not username:
			raise ValueError('O username deve ser informado.')
		setores = extra_fields.pop('setores', None)
		setor_inicial = setor or extra_fields.pop('setor', Setor.Codigo.NAO_ENCONTRADO)
		user = self.model(
			username=self.model.normalize_username(username),
			nome=nome,
			perfil=perfil,
			setor=setor_inicial,
			**extra_fields,
		)
		user.set_password(password)
		user.save(using=self._db)
		user.definir_setores(setores if setores is not None else [setor_inicial])
		return user

	def create_superuser(self, username, nome, perfil='GESTOR', setor='NAO_ENCONTRADO', password=None, **extra_fields):
		extra_fields.setdefault('is_staff', True)
		extra_fields.setdefault('is_superuser', True)
		extra_fields.setdefault('is_active', True)

		if extra_fields.get('is_staff') is not True:
			raise ValueError('Superuser deve ter is_staff=True.')
		if extra_fields.get('is_superuser') is not True:
			raise ValueError('Superuser deve ter is_superuser=True.')

		return self.create_user(
			username,
			nome,
			perfil,
			setor,
			password,
			setores=extra_fields.pop('setores', [Setor.Codigo.NAO_ENCONTRADO]),
			**extra_fields,
		)


class Usuario(BaseModel, AbstractBaseUser, PermissionsMixin):
	class Perfil(models.TextChoices):
		SEPARADOR = 'SEPARADOR', 'Separador'
		CONFERENTE = 'CONFERENTE', 'Conferente'
		GESTOR = 'GESTOR', 'Gestor'

	# Alias de compatibilidade para código legado que usa Usuario.Setor.*
	Setor = Setor.Codigo

	nome = models.CharField(max_length=100, verbose_name='nome', db_index=True)
	username = models.CharField(max_length=50, unique=True, verbose_name='username')
	perfil = models.CharField(max_length=20, choices=Perfil.choices, verbose_name='perfil')
	setor = models.CharField(max_length=20, choices=SETOR_CHOICES, verbose_name='setor')
	setores = models.ManyToManyField('usuarios.Setor', blank=True, related_name='usuarios', verbose_name='setores')
	is_active = models.BooleanField(default=True, verbose_name='ativo')
	is_staff = models.BooleanField(default=False, verbose_name='equipe')
	last_activity = models.DateTimeField(null=True, blank=True, verbose_name='ultima atividade', db_index=True)

	objects = UsuarioManager()

	USERNAME_FIELD = 'username'
	REQUIRED_FIELDS = ['nome', 'perfil', 'setor']

	class Meta:
		verbose_name = 'usuario'
		verbose_name_plural = 'usuarios'
		ordering = ('nome',)
		indexes = [
			models.Index(fields=['perfil', 'setor'], name='usuario_perf_set_idx'),
			models.Index(fields=['is_active'], name='usuario_active_idx'),
		]

	@property
	def senha(self):
		return self.password

	def __str__(self):
		return f'{self.nome} ({self.username})'

	@property
	def setores_nomes(self):
		return list(self.setores.values_list('nome', flat=True))

	@property
	def setores_display(self):
		return ' / '.join(self.setores_nomes) or self.setor

	def esta_online(self, janela_minutos=5):
		if not self.last_activity:
			return False
		return timezone.now() - self.last_activity <= timedelta(minutes=janela_minutos)

	def definir_setores(self, setores):
		if setores is None:
			return
		setores_normalizados = []
		for setor_nome in setores:
			setor_valor = (setor_nome or '').strip().upper()
			if not setor_valor:
				continue
			if setor_valor == 'FILTRO':
				setor_valor = self.Setor.FILTROS
			elif setor_valor == 'NAO ENCONTRADO':
				setor_valor = self.Setor.NAO_ENCONTRADO
			setores_normalizados.append(setor_valor)
		if not setores_normalizados:
			self.setores.clear()
			self.setor = self.Setor.NAO_ENCONTRADO
			self.save(update_fields=['setor', 'updated_at'])
			return
		setores_obj = [Setor.objects.get_or_create(nome=nome)[0] for nome in sorted(set(setores_normalizados))]
		self.setores.set(setores_obj)
		setor_primario = setores_obj[0].nome
		if self.setor != setor_primario:
			self.setor = setor_primario
			self.save(update_fields=['setor', 'updated_at'])


class UsuarioSessao(models.Model):
	usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='sessoes_monitoramento')
	ultimo_acesso = models.DateTimeField(auto_now=True)
	data_login = models.DateTimeField(auto_now_add=True)
	ativo = models.BooleanField(default=True)
	total_logins_dia = models.IntegerField(default=0)

	class Meta:
		verbose_name = 'sessao de usuario'
		verbose_name_plural = 'sessoes de usuarios'
		ordering = ('-ultimo_acesso',)

	def esta_online(self):
		return timezone.now() - self.ultimo_acesso <= timedelta(minutes=5)
