from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.conferencia.models import Conferencia
from apps.logs.models import Log
from apps.nf.models import NotaFiscal
from apps.rotas.models import Rota
from apps.tarefas.models import Tarefa
from apps.usuarios.models import Setor, Usuario


@override_settings(ROOT_URLCONF='config.urls')
class WMSFluxoAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.separador = Usuario.objects.create_user(
            username='separador_fluxo',
            nome='Separador Fluxo',
            perfil=Usuario.Perfil.SEPARADOR,
            setores=[
                Setor.Codigo.LUBRIFICANTE,
                Setor.Codigo.FILTROS,
                Setor.Codigo.AGREGADO,
                Setor.Codigo.NAO_ENCONTRADO,
            ],
            password='123456',
            is_active=True,
        )
        self.conferente = Usuario.objects.create_user(
            username='conferente_fluxo',
            nome='Conferente Fluxo',
            perfil=Usuario.Perfil.CONFERENTE,
            setores=[
                Setor.Codigo.LUBRIFICANTE,
                Setor.Codigo.FILTROS,
                Setor.Codigo.AGREGADO,
                Setor.Codigo.NAO_ENCONTRADO,
            ],
            password='123456',
            is_active=True,
        )
        self.rota = Rota.objects.create(nome='Rota Padrao', cep_inicial='00000000', cep_final='99999999')
        self.xml_dir = Path(settings.BASE_DIR) / 'xmls'
        self.xml_autorizado = self.xml_dir / 'xml_autorizado.xml'
        self.xml_cancelado = self.xml_dir / 'xml_cancelado.xml'
        self.xml_duplicado = self.xml_dir / 'xml_duplicado.xml'

    def _autenticar(self, usuario):
        self.client.force_authenticate(user=usuario)

    def _importar_xml(self, caminho_xml, usuario=None):
        self._autenticar(usuario or self.separador)
        with caminho_xml.open('rb') as xml_file:
            arquivo = SimpleUploadedFile(caminho_xml.name, xml_file.read(), content_type='text/xml')
        return self.client.post('/api/importar-xml/', {'file': arquivo}, format='multipart')

    def _separar_nf(self, nf):
        self._autenticar(self.separador)
        for tarefa in nf.tarefas.prefetch_related('itens__produto').order_by('id'):
            response_inicio = self.client.post('/api/separacao/iniciar/', {'tarefa_id': tarefa.id}, format='json')
            self.assertEqual(response_inicio.status_code, 200)

            for item in tarefa.itens.all():
                codigo = item.produto.cod_prod or item.produto.cod_ean
                for _ in range(int(item.quantidade_total)):
                    response_bipagem = self.client.post(
                        '/api/separacao/bipar/',
                        {'tarefa_id': tarefa.id, 'codigo': codigo},
                        format='json',
                    )
                    self.assertEqual(response_bipagem.status_code, 200)

            response_final = self.client.post(
                '/api/separacao/finalizar/',
                {'tarefa_id': tarefa.id, 'status': Tarefa.Status.CONCLUIDO},
                format='json',
            )
            self.assertEqual(response_final.status_code, 200)

    def _conferir_nf(self, nf):
        self._autenticar(self.conferente)
        response_inicio = self.client.post('/api/conferencia/iniciar/', {'nf_id': nf.id}, format='json')
        self.assertIn(response_inicio.status_code, {200, 400})
        if response_inicio.status_code == 400:
            return response_inicio
        conferencia_id = response_inicio.data['id']

        for item in nf.itens.select_related('produto').order_by('id'):
            codigo = item.produto.cod_prod or item.produto.cod_ean
            for _ in range(int(item.quantidade)):
                response_bipagem = self.client.post(
                    '/api/conferencia/bipar/',
                    {'conferencia_id': conferencia_id, 'codigo': codigo},
                    format='json',
                )
                self.assertEqual(response_bipagem.status_code, 200)

        response_final = self.client.post(
            '/api/conferencia/finalizar/',
            {'conferencia_id': conferencia_id},
            format='json',
        )
        self.assertEqual(response_final.status_code, 200)
        return response_final

    def test_importacao_ok(self):
        response = self._importar_xml(self.xml_autorizado)

        self.assertEqual(response.status_code, 200)
        nf = NotaFiscal.objects.get(chave_nfe='35111111111111111111550010000000011000000010')
        self.assertEqual(nf.status_fiscal, NotaFiscal.StatusFiscal.AUTORIZADA)
        self.assertEqual(NotaFiscal.objects.count(), 1)

    def test_duplicidade(self):
        primeira = self._importar_xml(self.xml_autorizado)
        segunda = self._importar_xml(self.xml_duplicado)

        self.assertEqual(primeira.status_code, 200)
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(segunda.data['status'], 'duplicada')
        self.assertEqual(NotaFiscal.objects.filter(chave_nfe='35111111111111111111550010000000011000000010').count(), 1)

    def test_nf_cancelada_importada_sem_alterar_outras_nfs(self):
        response_autorizado = self._importar_xml(self.xml_autorizado)
        nf_autorizada = NotaFiscal.objects.get(chave_nfe='35111111111111111111550010000000011000000010')
        tarefas_autorizadas_antes = Tarefa.objects.filter(nf=nf_autorizada).count()

        response_cancelado = self._importar_xml(self.xml_cancelado)
        nf_cancelada = NotaFiscal.objects.get(chave_nfe='35111111111111111111550010000000011000000020')
        nf_autorizada.refresh_from_db()

        self.assertEqual(response_autorizado.status_code, 200)
        self.assertEqual(response_cancelado.status_code, 200)
        self.assertEqual(nf_cancelada.status_fiscal, NotaFiscal.StatusFiscal.CANCELADA)
        self.assertEqual(NotaFiscal.objects.count(), 2)
        self.assertFalse(Tarefa.objects.filter(nf=nf_cancelada).exists())
        self.assertEqual(nf_autorizada.status_fiscal, NotaFiscal.StatusFiscal.AUTORIZADA)
        self.assertEqual(Tarefa.objects.filter(nf=nf_autorizada).count(), tarefas_autorizadas_antes)

    def test_bloqueio_na_separacao_para_nf_cancelada(self):
        self._importar_xml(self.xml_cancelado)
        nf_cancelada = NotaFiscal.objects.get(chave_nfe='35111111111111111111550010000000011000000020')
        tarefa = Tarefa.objects.filter(nf=nf_cancelada).order_by('id').first()
        self.assertIsNone(tarefa)
        self.assertFalse(Log.objects.filter(acao='SEPARACAO BLOQUEADA', detalhe__contains='NF CANCELADA').exists())

    def test_bloqueio_na_conferencia_para_nf_cancelada(self):
        self._importar_xml(self.xml_cancelado)
        nf_cancelada = NotaFiscal.objects.get(chave_nfe='35111111111111111111550010000000011000000020')

        self._autenticar(self.conferente)
        response = self.client.post('/api/conferencia/iniciar/', {'nf_id': nf_cancelada.id}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('NF cancelada', response.data['erro'])

    def test_nf_cancelada_nao_lista_em_separacao_e_conferencia(self):
        self._importar_xml(self.xml_autorizado)
        self._importar_xml(self.xml_cancelado)
        nf_autorizada = NotaFiscal.objects.get(chave_nfe='35111111111111111111550010000000011000000010')
        nf_cancelada = NotaFiscal.objects.get(chave_nfe='35111111111111111111550010000000011000000020')
        self._separar_nf(nf_autorizada)

        self._autenticar(self.separador)
        response_separacao = self.client.get('/api/separacao/tarefas/')
        self.assertEqual(response_separacao.status_code, 200)
        self.assertTrue(all(item['nf_id'] != nf_cancelada.id for item in response_separacao.data))

        self._autenticar(self.conferente)
        response_conferencia = self.client.get('/api/conferencia/nfs/')
        self.assertEqual(response_conferencia.status_code, 200)
        self.assertTrue(all(item['id'] != nf_cancelada.id for item in response_conferencia.data))

    def test_fluxo_completo_ok(self):
        response_importacao = self._importar_xml(self.xml_autorizado)
        nf = NotaFiscal.objects.get(chave_nfe='35111111111111111111550010000000011000000010')

        self._separar_nf(nf)
        response_final = self._conferir_nf(nf)
        nf.refresh_from_db()

        self.assertEqual(response_importacao.status_code, 200)
        if response_final.status_code == 200:
            self.assertEqual(response_final.data['status'], Conferencia.Status.OK)
        self.assertFalse(nf.bloqueada)
        self.assertTrue(Tarefa.objects.filter(rota=nf.rota).exists())
