from django.test import Client, TestCase, override_settings

from apps.usuarios.models import Setor, Usuario


@override_settings(ROOT_URLCONF='config.urls')
class UsuariosSetoresWebTests(TestCase):
    def setUp(self):
        Setor.garantir_setores_padrao()
        self.client = Client()
        self.gestor = Usuario.objects.create_user(
            username='gestor_usuarios',
            nome='Gestor Usuarios',
            perfil=Usuario.Perfil.GESTOR,
            setores=[Setor.Codigo.NAO_ENCONTRADO],
            password='123456',
            is_active=True,
        )
        self.client.login(username='gestor_usuarios', password='123456')
        self.setor_agregado = Setor.objects.get(nome=Setor.Codigo.AGREGADO)
        self.setor_filtros = Setor.objects.get(nome=Setor.Codigo.FILTROS)

    def test_criar_usuario_com_setores_por_id(self):
        response = self.client.post(
            '/usuarios/',
            {
                'nome': 'Separador 01',
                'username': 'separador01',
                'senha': '123456',
                'perfil': Usuario.Perfil.SEPARADOR,
                'setores': [str(self.setor_agregado.id), str(self.setor_filtros.id)],
                'is_active': 'on',
            },
        )

        self.assertEqual(response.status_code, 302)
        usuario = Usuario.objects.get(username='separador01')
        self.assertEqual(usuario.setor, Setor.Codigo.AGREGADO)
        self.assertSetEqual(
            set(usuario.setores.values_list('nome', flat=True)),
            {Setor.Codigo.AGREGADO, Setor.Codigo.FILTROS},
        )

    def test_editar_usuario_com_setores_por_id(self):
        usuario = Usuario.objects.create_user(
            username='separador_edit',
            nome='Separador Edit',
            perfil=Usuario.Perfil.SEPARADOR,
            setores=[Setor.Codigo.NAO_ENCONTRADO],
            password='123456',
            is_active=True,
        )

        response = self.client.post(
            f'/usuarios/{usuario.id}/editar/',
            {
                'nome': 'Separador Editado',
                'username': 'separador_edit',
                'perfil': Usuario.Perfil.SEPARADOR,
                'setores': [str(self.setor_filtros.id)],
                'is_active': 'on',
            },
        )

        self.assertEqual(response.status_code, 302)
        usuario.refresh_from_db()
        self.assertEqual(usuario.nome, 'Separador Editado')
        self.assertEqual(usuario.setor, Setor.Codigo.FILTROS)
        self.assertSetEqual(set(usuario.setores.values_list('nome', flat=True)), {Setor.Codigo.FILTROS})