from django.db import migrations


def popular_setores_usuario(apps, schema_editor):
    Usuario = apps.get_model('usuarios', 'Usuario')
    SetorUsuario = apps.get_model('usuarios', 'SetorUsuario')

    for usuario in Usuario.objects.all().iterator():
        setor_nome = (getattr(usuario, 'setor', None) or '').strip().upper()
        if not setor_nome:
            continue
        if setor_nome == 'FILTRO':
            setor_nome = 'FILTROS'
        elif setor_nome == 'NAO ENCONTRADO':
            setor_nome = 'NAO_ENCONTRADO'
        setor_obj, _ = SetorUsuario.objects.get_or_create(nome=setor_nome)
        usuario.setores.add(setor_obj)


def reverter_popular_setores_usuario(apps, schema_editor):
    Usuario = apps.get_model('usuarios', 'Usuario')
    for usuario in Usuario.objects.all().iterator():
        usuario.setores.clear()


class Migration(migrations.Migration):
    dependencies = [
        ('usuarios', '0004_setorusuario_usuario_setores'),
    ]

    operations = [
        migrations.RunPython(popular_setores_usuario, reverter_popular_setores_usuario),
    ]
