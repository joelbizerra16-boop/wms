from unittest.mock import patch

from django.db.utils import ProgrammingError
from django.test import TestCase
from django.utils import timezone

from apps.clientes.models import Cliente
from apps.nf.models import NotaFiscal, NotaFiscalItem
from apps.nf.services.importador_xml import gerar_tarefas_separacao
from apps.produtos.models import Produto
from apps.rotas.models import Rota
from apps.tarefas.models import Tarefa, TarefaItem
from apps.usuarios.models import Setor


class WaveFallbackTests(TestCase):
	def setUp(self):
		self.rota = Rota.objects.create(nome='Guarulhos', cep_inicial='01000000', cep_final='09999999')
		self.cliente = Cliente.objects.create(nome='Cliente Fallback', inscricao_estadual='123456')
		self.produto = Produto.objects.create(
			cod_prod='LUB001',
			descricao='Lubrificante',
			cod_ean='7891001',
			setor=Setor.Codigo.LUBRIFICANTE,
			categoria=Produto.Categoria.LUBRIFICANTE,
			embalagem='CX',
		)
		self.nf = NotaFiscal.objects.create(
			chave_nfe='35111111111111111111550010000000011000000111',
			numero='1111',
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.PENDENTE,
			data_emissao=timezone.now(),
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=False,
			ativa=True,
		)
		NotaFiscalItem.objects.create(nf=self.nf, produto=self.produto, quantidade='2.00')

	@patch('apps.nf.services.importador_xml.obter_ou_criar_tarefa_onda')
	def test_importacao_faz_fallback_classico_quando_onda_falha(self, obter_ou_criar_tarefa_onda_mock):
		obter_ou_criar_tarefa_onda_mock.side_effect = ProgrammingError('relation "tarefas_ondaseparacao" does not exist')

		gerar_tarefas_separacao(self.nf, tarefas_lote_cache={})

		tarefa = Tarefa.objects.get(rota=self.rota, setor=Setor.Codigo.LUBRIFICANTE)
		item = TarefaItem.objects.get(tarefa=tarefa, nf=self.nf, produto=self.produto)

		self.assertIsNone(tarefa.onda_id)
		self.assertEqual(tarefa.tipo, Tarefa.Tipo.ROTA)
		self.assertEqual(item.quantidade_total, self.nf.itens.first().quantidade)