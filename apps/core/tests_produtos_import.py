from io import BytesIO

import pandas as pd
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.services.cadastro_import_service import importar_produtos_arquivo
from apps.core.services.tarefa_importacao_bloqueio_service import (
    ImportacaoProdutosBloqueadaError,
    montar_diagnostico_operacional_tarefa,
)
from apps.produtos.models import Produto
from apps.rotas.models import Rota
from apps.tarefas.models import Tarefa, TarefaItem
from apps.usuarios.models import Setor, Usuario


class ImportacaoProdutosExcelTests(TestCase):
    def _build_excel_file(self, rows):
        dataframe = pd.DataFrame(rows)
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            dataframe.to_excel(writer, index=False)
        buffer.seek(0)
        buffer.name = 'produtos.xlsx'
        return buffer

    def test_importacao_produtos_preserva_ean_grande_como_texto(self):
        arquivo = self._build_excel_file(
            [
                {
                    'COD_PROD': '11803',
                    'Código': '11803',
                    'Descrição': 'HOT WHEELS CITY PISTA ATAQUE DO CROCODIL',
                    'EMBALAGEM': 'PC',
                    'Código de Barras (EAN)': '194735109630',
                    'SETOR': 'AGREGADO',
                }
            ]
        )

        resultado = importar_produtos_arquivo(arquivo)

        self.assertEqual(resultado['criados'], 1)
        produto = Produto.objects.get(cod_prod='11803')
        self.assertEqual(produto.codigo, '11803')
        self.assertEqual(produto.cod_ean, '194735109630')
        self.assertEqual(produto.setor, 'AGREGADO')

    def test_importacao_produtos_ignora_nan_sem_quebrar_upload(self):
        arquivo = self._build_excel_file(
            [
                {
                    'COD_PROD': '14625',
                    'Código': '14625',
                    'Descrição': 'CF850/2 MANN',
                    'EMBALAGEM': 'PC',
                    'Código de Barras (EAN)': '',
                    'SETOR': 'FILTRO',
                }
            ]
        )

        resultado = importar_produtos_arquivo(arquivo)

        self.assertEqual(resultado['criados'], 1)
        produto = Produto.objects.get(cod_prod='14625')
        self.assertIsNone(produto.cod_ean)

    def test_importacao_produtos_atualiza_existente_sem_alterar_campos_operacionais(self):
        produto = Produto.objects.create(
            cod_prod='20001',
            codigo='20001',
            descricao='DESCRICAO ANTIGA',
            cod_ean='789000000001',
            embalagem='CX',
            unidade='CX',
            setor='FILTROS',
            categoria=Produto.Categoria.FILTROS,
            ativo=True,
            cadastrado_manual=False,
            incompleto=True,
        )

        arquivo = self._build_excel_file(
            [
                {
                    'COD_PROD': '20001',
                    'Código': '20001',
                    'Descrição': 'DESCRICAO NOVA',
                    'EMBALAGEM': 'UN',
                    'Código de Barras (EAN)': '789000000999',
                    'SETOR': 'AGREGADO',
                }
            ]
        )

        resultado = importar_produtos_arquivo(arquivo)

        self.assertEqual(resultado['atualizados'], 1)
        produto.refresh_from_db()
        self.assertEqual(produto.descricao, 'DESCRICAO NOVA')
        self.assertEqual(produto.cod_ean, '789000000999')
        self.assertEqual(produto.embalagem, 'CX')
        self.assertEqual(produto.unidade, 'CX')
        self.assertEqual(produto.setor, 'FILTROS')
        self.assertEqual(produto.categoria, Produto.Categoria.FILTROS)
        self.assertFalse(produto.cadastrado_manual)
        self.assertTrue(produto.incompleto)

    def test_importacao_produtos_remove_sufixo_decimal_do_ean_sem_adicionar_zero(self):
        arquivo = self._build_excel_file(
            [
                {
                    'COD_PROD': '30001',
                    'Código': '30001',
                    'Descrição': 'PRODUTO EAN DECIMAL',
                    'EMBALAGEM': 'PC',
                    'Código de Barras (EAN)': '789123456789.0',
                    'SETOR': 'AGREGADO',
                }
            ]
        )

        resultado = importar_produtos_arquivo(arquivo)

        self.assertEqual(resultado['criados'], 1)
        produto = Produto.objects.get(cod_prod='30001')
        self.assertEqual(produto.cod_ean, '789123456789')

    def test_importacao_produtos_grande_processa_em_lotes_sem_falhar(self):
        rows = []
        for index in range(250):
            rows.append(
                {
                    'COD_PROD': f'BATCH{index:04d}',
                    'Código': f'BATCH{index:04d}',
                    'Descrição': f'PRODUTO LOTE {index}',
                    'EMBALAGEM': 'PC',
                    'Código de Barras (EAN)': f'789{index:09d}',
                    'SETOR': 'AGREGADO',
                }
            )

        arquivo = self._build_excel_file(rows)

        resultado = importar_produtos_arquivo(arquivo)

        self.assertEqual(resultado['criados'], 250)
        self.assertEqual(resultado['atualizados'], 0)
        self.assertEqual(Produto.objects.filter(cod_prod__startswith='BATCH').count(), 250)


class ImportacaoProdutosBloqueioTarefaTests(TestCase):
    def setUp(self):
        self.rota = Rota.objects.create(
            nome='AJUSTAR',
            nome_rota='AJUSTAR',
            cep_inicial='01000000',
            cep_final='01999999',
        )
        self.gestor = Usuario.objects.create_user(
            username='gestor_bloqueio',
            password='123456',
            nome='Gestor Bloqueio',
            perfil=Usuario.Perfil.GESTOR,
            setor=Setor.Codigo.NAO_ENCONTRADO,
        )
        Setor.objects.get_or_create(nome=Setor.Codigo.NAO_ENCONTRADO)
        self.gestor.setores.add(Setor.objects.get(nome=Setor.Codigo.NAO_ENCONTRADO))

    def _build_excel_file(self):
        dataframe = pd.DataFrame(
            [
                {
                    'COD_PROD': 'BLOQ001',
                    'Código': 'BLOQ001',
                    'Descrição': 'PRODUTO BLOQUEIO',
                    'EMBALAGEM': 'PC',
                    'Código de Barras (EAN)': '',
                    'SETOR': 'AGREGADO',
                }
            ]
        )
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            dataframe.to_excel(writer, index=False)
        buffer.seek(0)
        buffer.name = 'produtos.xlsx'
        return buffer

    def test_importacao_bloqueada_informa_tarefa_responsavel(self):
        Tarefa.objects.create(
            tipo=Tarefa.Tipo.ROTA,
            setor=Setor.Codigo.NAO_ENCONTRADO,
            rota=self.rota,
            status=Tarefa.Status.ABERTO,
            ativo=True,
        )

        with self.assertRaises(ImportacaoProdutosBloqueadaError) as ctx:
            importar_produtos_arquivo(self._build_excel_file())

        self.assertEqual(len(ctx.exception.tarefas), 1)
        self.assertIn('Importação bloqueada pela Tarefa #', str(ctx.exception))
        self.assertEqual(ctx.exception.tarefas[0]['tipo'], Tarefa.Tipo.ROTA)
        self.assertIn('url_localizar', ctx.exception.tarefas[0])

    def test_importacao_liberada_sem_tarefa_ativa(self):
        resultado = importar_produtos_arquivo(self._build_excel_file())
        self.assertEqual(resultado['criados'], 1)

    def test_importacao_bloqueada_nao_considera_tarefa_inativa(self):
        Tarefa.objects.create(
            tipo=Tarefa.Tipo.ROTA,
            setor=Setor.Codigo.NAO_ENCONTRADO,
            rota=self.rota,
            status=Tarefa.Status.ABERTO,
            ativo=False,
        )
        resultado = importar_produtos_arquivo(self._build_excel_file())
        self.assertEqual(resultado['criados'], 1)

    def test_produtos_web_exibe_painel_de_bloqueio(self):
        tarefa = Tarefa.objects.create(
            tipo=Tarefa.Tipo.ROTA,
            setor=Setor.Codigo.NAO_ENCONTRADO,
            rota=self.rota,
            status=Tarefa.Status.ABERTO,
            ativo=True,
        )
        client = Client()
        client.force_login(self.gestor)
        session = client.session
        session['importacao_bloqueio_tarefas'] = [
            {
                'id': tarefa.id,
                'tipo': tarefa.tipo,
                'status': tarefa.status,
                'setor': tarefa.setor,
                'criacao_data': '16/05/2026',
                'dias_parada': 40,
                'nfs': ['1415602'],
                'rota': 'AJUSTAR',
                'produtos': ['14733'],
                'usuario': '',
                'url_localizar': f'/separacao/{tarefa.id}/',
            }
        ]
        session.save()

        response = client.get(reverse('web-produtos'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Importação bloqueada por tarefa operacional')
        self.assertContains(response, f'Tarefa #{tarefa.id}')
        self.assertContains(response, 'Localizar tarefa')
        self.assertContains(response, f'/separacao/{tarefa.id}/')

    def test_separacao_mostrar_antigas_exibe_tarefa_fora_do_periodo(self):
        produto = Produto.objects.create(
            cod_prod='ORFAO001',
            descricao='Produto orfao',
            setor='NAO_ENCONTRADO',
            categoria=Produto.Categoria.NAO_ENCONTRADO,
        )
        tarefa = Tarefa.objects.create(
            tipo=Tarefa.Tipo.ROTA,
            setor=Setor.Codigo.NAO_ENCONTRADO,
            rota=self.rota,
            status=Tarefa.Status.ABERTO,
            ativo=True,
        )
        TarefaItem.objects.create(
            tarefa=tarefa,
            produto=produto,
            quantidade_total=10,
            quantidade_separada=0,
        )
        Tarefa.objects.filter(id=tarefa.id).update(
            created_at=timezone.now() - timezone.timedelta(days=40),
            updated_at=timezone.now() - timezone.timedelta(days=40),
        )

        client = Client()
        client.force_login(self.gestor)

        response_padrao = client.get(reverse('web-separacao-lista'))
        self.assertNotContains(response_padrao, f'>{tarefa.id}<')

        response_antigas = client.get(reverse('web-separacao-lista'), {'mostrar_antigas': '1'})
        self.assertContains(response_antigas, f'>{tarefa.id}<')
        self.assertContains(response_antigas, 'Mostrar tarefas antigas')

    def test_diagnostico_operacional_tarefa(self):
        tarefa = Tarefa.objects.create(
            tipo=Tarefa.Tipo.ROTA,
            setor=Setor.Codigo.NAO_ENCONTRADO,
            rota=self.rota,
            status=Tarefa.Status.ABERTO,
            ativo=True,
        )
        diagnostico = montar_diagnostico_operacional_tarefa(tarefa)
        self.assertEqual(diagnostico['id'], tarefa.id)
        self.assertIn('dias_parada', diagnostico)
        self.assertIn('auditoria', diagnostico)
        self.assertEqual(diagnostico['possui_separacao'], False)

    def test_localizar_tarefa_abre_execucao(self):
        tarefa = Tarefa.objects.create(
            tipo=Tarefa.Tipo.ROTA,
            setor=Setor.Codigo.NAO_ENCONTRADO,
            rota=self.rota,
            status=Tarefa.Status.ABERTO,
            ativo=True,
        )
        client = Client()
        client.force_login(self.gestor)
        response = client.get(reverse('web-separacao-exec', args=[tarefa.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Diagnóstico operacional')
        self.assertContains(response, 'Dias parada')