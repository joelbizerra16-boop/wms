from django.db import migrations


def garantir_setores_padrao(apps, schema_editor):
    Setor = apps.get_model('usuarios', 'Setor')
    for nome in ['LUBRIFICANTE', 'FILTROS', 'AGREGADO', 'NAO_ENCONTRADO']:
        Setor.objects.get_or_create(nome=nome)


class Migration(migrations.Migration):
    dependencies = [
        ('usuarios', '0006_rename_setorusuario_setor'),
    ]

    operations = [
        migrations.RunPython(garantir_setores_padrao, migrations.RunPython.noop),
    ]
