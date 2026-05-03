from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("usuarios", "0002_remove_usuario_usuario_username_idx"),
    ]

    operations = [
        migrations.CreateModel(
            name="UsuarioSessao",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ultimo_acesso", models.DateTimeField(auto_now=True)),
                ("data_login", models.DateTimeField(auto_now_add=True)),
                ("ativo", models.BooleanField(default=True)),
                ("total_logins_dia", models.IntegerField(default=0)),
                (
                    "usuario",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sessoes_monitoramento",
                        to="usuarios.usuario",
                    ),
                ),
            ],
            options={
                "verbose_name": "sessao de usuario",
                "verbose_name_plural": "sessoes de usuarios",
                "ordering": ("-ultimo_acesso",),
            },
        ),
    ]
