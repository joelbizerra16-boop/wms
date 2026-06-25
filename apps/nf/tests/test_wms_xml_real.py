from pathlib import Path
import xml.etree.ElementTree as ET

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Q
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.test_bipagem_helpers import bipar_codigo

from apps.logs.models import Log
from apps.nf.models import NotaFiscal
from apps.rotas.models import Rota
from apps.tarefas.models import Tarefa
from apps.usuarios.models import Setor, Usuario


@override_settings(ROOT_URLCONF='config.urls')
class WMSXMLRealAPITests(TestCase):
    STATUS_AUTORIZADA = {'100'}
    STATUS_CANCELADA = {'101', '135', '151', '155'}
    NAMESPACE = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = APIClient()
        self.separador = Usuario.objects.create_user(
            username='separador_xml_real',
            nome='Separador XML Real',
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
            username='conferente_xml_real',
            nome='Conferente XML Real',
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
        self.rota = Rota.objects.create(nome='Rota XML Real', cep_inicial='00000000', cep_final='99999999')
        self.xml_dir = Path(settings.BASE_DIR) / 'xmls'
        self.xml_files = sorted(self.xml_dir.glob('procNFe*.xml'))
        self._tarefas_separadas = set()

    def _api_payload(self, response):
        body = response.data
        if isinstance(body, dict) and isinstance(body.get('data'), dict):
            return body['data']
        if isinstance(body, dict) and 'id' in body:
            return body
        return body

    def _api_list(self, response):
        body = response.data
        if isinstance(body, dict):
            payload = body.get('data', body)
            return payload if isinstance(payload, list) else []
        return body if isinstance(body, list) else []

    def _autenticar(self, usuario):
        self.client.force_authenticate(user=usuario)

    def _detectar_status_xml(self, caminho_xml):
        root = ET.parse(caminho_xml).getroot()
        cstat = root.findtext('.//nfe:protNFe/nfe:infProt/nfe:cStat', default='', namespaces=self.NAMESPACE).strip()
        chave = root.find('.//nfe:infNFe', self.NAMESPACE)
        chave_nfe = ''
        if chave is not None:
            chave_nfe = (chave.attrib.get('Id') or '').replace('NFe', '').strip()
        if cstat in self.STATUS_AUTORIZADA:
            return {'status': NotaFiscal.StatusFiscal.AUTORIZADA, 'cstat': cstat, 'chave_nfe': chave_nfe}
        if cstat in self.STATUS_CANCELADA:
            return {'status': NotaFiscal.StatusFiscal.CANCELADA, 'cstat': cstat, 'chave_nfe': chave_nfe}
        return {'status': None, 'cstat': cstat, 'chave_nfe': chave_nfe}

    def _importar_xml(self, caminho_xml, usuario=None):
        self._autenticar(usuario or self.separador)
        with caminho_xml.open('rb') as xml_file:
            arquivo = SimpleUploadedFile(caminho_xml.name, xml_file.read(), content_type='text/xml')
        return self.client.post('/api/importar-xml/', {'file': arquivo}, format='multipart')

    def _separar_tarefa(self, tarefa):
        tarefa.refresh_from_db()
        if tarefa.status == Tarefa.Status.CONCLUIDO:
            return
        self._autenticar(self.separador)
        response_inicio = self.client.post('/api/separacao/iniciar/', {'tarefa_id': tarefa.id}, format='json')
        self.assertEqual(response_inicio.status_code, 200)

        for item in tarefa.itens.select_related('produto').all():
            if item.quantidade_separada >= item.quantidade_total:
                continue
            codigo = item.produto.cod_prod or item.produto.cod_ean
            pendente = int(item.quantidade_total - item.quantidade_separada)
            for response_bipagem in bipar_codigo(
                self.client,
                '/api/separacao/bipar/',
                {'tarefa_id': tarefa.id},
                codigo,
                pendente,
            ):
                self.assertEqual(response_bipagem.status_code, 200)

        response_final = self.client.post(
            '/api/separacao/finalizar/',
            {'tarefa_id': tarefa.id, 'status': Tarefa.Status.CONCLUIDO},
            format='json',
        )
        self.assertEqual(response_final.status_code, 200)

    def _separar_nf(self, nf):
        tarefas = (
            Tarefa.objects.filter(Q(nf=nf) | Q(itens__nf=nf))
            .distinct()
            .prefetch_related('itens__produto')
            .order_by('id')
        )
        for tarefa in tarefas:
            if tarefa.id in self._tarefas_separadas:
                continue
            self._separar_tarefa(tarefa)
            self._tarefas_separadas.add(tarefa.id)

    def _iniciar_conferencia(self, nf):
        self._autenticar(self.conferente)
        return self.client.post('/api/conferencia/iniciar/', {'nf_id': nf.id}, format='json')

    def _conferir_nf(self, nf):
        response_inicio = self._iniciar_conferencia(nf)
        self.assertEqual(response_inicio.status_code, 200)
        conferencia_id = self._api_payload(response_inicio)['id']

        for item in nf.itens.select_related('produto').order_by('id'):
            codigo = item.produto.cod_prod or item.produto.cod_ean
            for _ in range(int(item.quantidade)):
                response_bipagem = self.client.post(
                    '/api/conferencia/bipar/',
                    {'conferencia_id': conferencia_id, 'codigo': codigo},
                    format='json',
                )
                self.assertEqual(response_bipagem.status_code, 200)

        response_final = self.client.post('/api/conferencia/finalizar/', {'conferencia_id': conferencia_id}, format='json')
        self.assertEqual(response_final.status_code, 200)
        return response_final

    def test_fluxo_wms_com_xmls_reais(self):
        if not self.xml_files:
            self.skipTest('Nenhum XML real encontrado em BASE_DIR/xmls.')

        nfs_autorizadas = []
        nfs_canceladas = []

        for caminho_xml in self.xml_files:
            metadados = self._detectar_status_xml(caminho_xml)
            if metadados['status'] is None:
                continue

            with self.subTest(xml=caminho_xml.name, cstat=metadados['cstat']):
                response = self._importar_xml(caminho_xml)
                self.assertEqual(response.status_code, 200)
                self.assertIn(response.data['status'], {'sucesso', 'duplicada'})
                nf = NotaFiscal.objects.get(chave_nfe=metadados['chave_nfe'])
                self.assertEqual(nf.status_fiscal, metadados['status'])
                if metadados['status'] == NotaFiscal.StatusFiscal.AUTORIZADA:
                    nfs_autorizadas.append(nf.id)
                elif metadados['status'] == NotaFiscal.StatusFiscal.CANCELADA:
                    nfs_canceladas.append(nf.id)

        self.assertTrue(nfs_autorizadas, 'Nenhuma NF autorizada encontrada para teste operacional.')

        for nf_id in nfs_autorizadas:
            nf = NotaFiscal.objects.prefetch_related('itens__produto', 'tarefas__itens__produto').get(id=nf_id)
            with self.subTest(nf=nf.numero, status='AUTORIZADA'):
                self._separar_nf(nf)
                response_inicio_conferencia = self._iniciar_conferencia(nf)
                self.assertIn(response_inicio_conferencia.status_code, {200, 400})
                if response_inicio_conferencia.status_code == 400:
                    continue
                conferencia_id = self._api_payload(response_inicio_conferencia)['id']
                for item in nf.itens.select_related('produto').order_by('id'):
                    codigo = item.produto.cod_prod or item.produto.cod_ean
                    for response_bipagem in bipar_codigo(
                        self.client,
                        '/api/conferencia/bipar/',
                        {'conferencia_id': conferencia_id},
                        codigo,
                        item.quantidade,
                    ):
                        self.assertEqual(response_bipagem.status_code, 200)
                response_final = self.client.post(
                    '/api/conferencia/finalizar/',
                    {'conferencia_id': conferencia_id},
                    format='json',
                )
                self.assertEqual(response_final.status_code, 200)

        if not nfs_canceladas:
            self.skipTest('Nenhuma NF cancelada encontrada em xmls/ para teste operacional.')
        else:
            for nf_id in nfs_canceladas:
                nf = NotaFiscal.objects.prefetch_related('itens__produto').get(id=nf_id)
                tarefa = (
                    Tarefa.objects.filter(Q(nf=nf) | Q(itens__nf=nf))
                    .distinct()
                    .order_by('id')
                    .first()
                )
                self.assertIsNotNone(tarefa)

                with self.subTest(nf=nf.numero, status='CANCELADA-SEPARACAO'):
                    self._autenticar(self.separador)
                    response_separacao = self.client.post('/api/separacao/iniciar/', {'tarefa_id': tarefa.id}, format='json')
                    self.assertEqual(response_separacao.status_code, 400)
                    self.assertIn('NF cancelada', response_separacao.data['erro'])

                with self.subTest(nf=nf.numero, status='CANCELADA-CONFERENCIA'):
                    self._autenticar(self.conferente)
                    response_conferencia = self.client.post('/api/conferencia/iniciar/', {'nf_id': nf.id}, format='json')
                    self.assertEqual(response_conferencia.status_code, 400)
                    self.assertIn('NF cancelada', response_conferencia.data['erro'])

            self.assertTrue(Log.objects.filter(acao='SEPARACAO BLOQUEADA', detalhe__contains='NF CANCELADA').exists())

        self._autenticar(self.separador)
        response_tarefas = self.client.get('/api/separacao/tarefas/')
        self.assertEqual(response_tarefas.status_code, 200)
        self.assertTrue(all(item.get('nf_id') not in nfs_canceladas for item in self._api_list(response_tarefas)))

        self._autenticar(self.conferente)
        response_nfs = self.client.get('/api/conferencia/nfs/')
        self.assertEqual(response_nfs.status_code, 200)
        self.assertTrue(all(item['id'] not in nfs_canceladas for item in self._api_list(response_nfs)))
