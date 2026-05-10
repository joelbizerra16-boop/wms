import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Cria superuser automaticamente em produção (Render)"

    def handle(self, *args, **kwargs):
        user_model = get_user_model()
        field_names = {field.name for field in user_model._meta.fields}

        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "123456")
        nome = os.environ.get("DJANGO_SUPERUSER_NOME", username)
        perfil = os.environ.get("DJANGO_SUPERUSER_PERFIL", "GESTOR")
        setor = os.environ.get("DJANGO_SUPERUSER_SETOR", "NAO_ENCONTRADO")

        if not user_model.objects.filter(username=username).exists():
            create_kwargs = {
                "username": username,
                "password": password,
            }

            if "nome" in field_names:
                create_kwargs["nome"] = nome
            if "perfil" in field_names:
                create_kwargs["perfil"] = perfil
            if "setor" in field_names:
                create_kwargs["setor"] = setor
            if "email" in field_names:
                create_kwargs["email"] = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@wms.com")

            user_model.objects.create_superuser(**create_kwargs)
            self.stdout.write(self.style.SUCCESS(f"Superuser {username} criado com sucesso"))
        else:
            self.stdout.write(self.style.WARNING(f"Superuser {username} já existe"))