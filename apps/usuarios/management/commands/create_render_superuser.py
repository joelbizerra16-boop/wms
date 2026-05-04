from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
import os


class Command(BaseCommand):
    help = "Cria superuser automaticamente em produção (Render)"

    def handle(self, *args, **kwargs):
        User = get_user_model()

        username = "admin2"
        email = "admin@wms.com"
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "123456")

        if not User.objects.filter(username=username).exists():
            User.objects.create_superuser(
                username=username,
                email=email,
                password=password
            )
            self.stdout.write(self.style.SUCCESS("Superuser admin2 criado com sucesso"))
        else:
            self.stdout.write(self.style.WARNING("Superuser admin2 já existe"))