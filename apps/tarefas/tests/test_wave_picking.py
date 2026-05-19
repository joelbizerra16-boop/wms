from django.test import TestCase
from django.utils import timezone

from apps.clientes.models import Cliente
from apps.conferencia.services.conferencia_service import pedido_esta_liberado_para_conferencia
from apps.nf.models import NotaFiscal, NotaFiscalItem
from apps.nf.services.importador_xml import gerar_tarefas_separacao
from apps.produtos.models import Produto
from apps.rotas.models import Rota
from apps.tarefas.models import OndaSeparacao, Tarefa, TarefaItem
from apps.usuarios.models import Setor


class WavePickingTests(TestCase):
	def setUp(self):
		self.rota = Rota.objects.create(nome='Guarulhos', cep_inicial='01000000', cep_final='09999999')
		self.cliente = Cliente.objects.create(nome='Cliente Wave', inscricao_estadual='123456789')
		self.produto_cx = Produto.objects.create(
			cod_prod='LUBCX',
			descricao='Lubrificante CX',
			cod_ean='789000001',
			setor=Setor.Codigo.LUBRIFICANTE,
			categoria=Produto.Categoria.LUBRIFICANTE,
			embalagem='CX',
		)
		self.produto_tb = Produto.objects.create(
			cod_prod='LUBTB',
			descricao='Lubrificante TB',
			cod_ean='789000002',
			setor=Setor.Codigo.LUBRIFICANTE,
			categoria=Produto.Categoria.LUBRIFICANTE,
			embalagem='TB',
		)

	def _criar_nf(self, numero):
		return NotaFiscal.objects.create(
			chave_nfe=f'3511111111111111111155001000000{numero:04d}00000099',
			numero=str(numero),
			cliente=self.cliente,
			rota=self.rota,
			status=NotaFiscal.Status.PENDENTE,
			data_emissao=timezone.now(),
			status_fiscal=NotaFiscal.StatusFiscal.AUTORIZADA,
			bloqueada=False,
			ativa=True,
		)

	def test_agrupar_onda_por_rota_setor_embalagem_limita_cinco_nfs(self):
		tarefas_lote_cache = {}
		for numero in range(1001, 1007):
			nf = self._criar_nf(numero)
			NotaFiscalItem.objects.create(nf=nf, produto=self.produto_cx, quantidade='1.00')
			gerar_tarefas_separacao(nf, tarefas_lote_cache=tarefas_lote_cache)

		ondas = list(
			OndaSeparacao.objects.filter(
				rota=self.rota,
				setor=Setor.Codigo.LUBRIFICANTE,
				tipo_embalagem='CX',
			).order_by('id')
		)

		self.assertEqual(len(ondas), 2)
		self.assertEqual([onda.nf_total for onda in ondas], [5, 1])
		self.assertEqual(Tarefa.objects.filter(onda__in=ondas).count(), 2)

	def test_mesma_nf_gera_ondas_distintas_por_multiembalagem(self):
		nf = self._criar_nf(2001)
		NotaFiscalItem.objects.create(nf=nf, produto=self.produto_cx, quantidade='4.00')
		NotaFiscalItem.objects.create(nf=nf, produto=self.produto_tb, quantidade='2.00')

		gerar_tarefas_separacao(nf, tarefas_lote_cache={})

		ondas = list(OndaSeparacao.objects.filter(nfs=nf).order_by('tipo_embalagem'))
		self.assertEqual([onda.tipo_embalagem for onda in ondas], ['CX', 'TB'])
		self.assertEqual(Tarefa.objects.filter(onda__in=ondas).count(), 2)

	def test_conferencia_so_libera_quando_todas_embalagens_da_nf_estao_separadas(self):
		nf = self._criar_nf(3001)
		NotaFiscalItem.objects.create(nf=nf, produto=self.produto_cx, quantidade='4.00')
		NotaFiscalItem.objects.create(nf=nf, produto=self.produto_tb, quantidade='2.00')

		gerar_tarefas_separacao(nf, tarefas_lote_cache={})

		item_cx = TarefaItem.objects.get(nf=nf, produto=self.produto_cx)
		item_tb = TarefaItem.objects.get(nf=nf, produto=self.produto_tb)
		item_cx.quantidade_separada = item_cx.quantidade_total
		item_cx.save(update_fields=['quantidade_separada', 'updated_at'])

		validacao_parcial = pedido_esta_liberado_para_conferencia(nf)
		self.assertFalse(validacao_parcial['liberado'])
		self.assertEqual(validacao_parcial['status_separacao'], 'PARCIALMENTE_SEPARADA')

		item_tb.quantidade_separada = item_tb.quantidade_total
		item_tb.save(update_fields=['quantidade_separada', 'updated_at'])

		validacao_final = pedido_esta_liberado_para_conferencia(nf)
		self.assertTrue(validacao_final['liberado'])
		self.assertEqual(validacao_final['status_separacao'], 'SEPARADO')