from collections import defaultdict
from decimal import Decimal
from io import BytesIO
import logging
import re
import traceback
import xml.etree.ElementTree as ET
import zipfile

from django.contrib import messages
from django.db.models import Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.services.minuta_service import (
    MinutaImportacaoError,
    consultar_minuta_itens_queryset,
    confirmar_importacao_minuta,
    get_minuta_inconsistencias,
    listar_minuta_itens,
    montar_preview_importacao_minuta,
)
from apps.nf.models import EntradaNF, NotaFiscal, NotaFiscalItem, nota_fiscal_bairro_valor
from apps.nf.services.xml_storage_service import XMLStorageUnavailableError, open_entrada_xml
from apps.usuarios.access import build_access_context, require_profiles
from apps.usuarios.models import Usuario


logger = logging.getLogger(__name__)

MINUTA_PREVIEW_SESSION_KEY = 'minuta_import_preview'
SEFAZ_NS = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}


def _registrar_fontes_minuta():
    try:
        pdfmetrics.registerFont(TTFont('MinutaSegoe', r'C:\Windows\Fonts\segoeui.ttf'))
        pdfmetrics.registerFont(TTFont('MinutaSegoeBold', r'C:\Windows\Fonts\segoeuib.ttf'))
        pdfmetrics.registerFont(TTFont('MinutaSegoeLight', r'C:\Windows\Fonts\segoeuil.ttf'))
        return {
            'regular': 'MinutaSegoe',
            'bold': 'MinutaSegoeBold',
            'light': 'MinutaSegoeLight',
        }
    except Exception:
        return {
            'regular': 'Helvetica',
            'bold': 'Helvetica-Bold',
            'light': 'Helvetica',
        }


def _formatar_decimal_pdf(valor, casas=2):
    numero = valor if valor is not None else Decimal('0')
    formato = f'{{:.{casas}f}}'
    return formato.format(numero).replace('.', ',')


def _formatar_quantidade_pdf(valor):
    numero = valor if valor is not None else Decimal('0')
    if numero == numero.to_integral():
        return str(int(numero))
    return _formatar_decimal_pdf(numero, 2).rstrip('0').rstrip(',')


def _formatar_moeda_pdf(valor):
    return f'R$ {_formatar_decimal_pdf(valor, 2)}'


def _texto_xml(node, path):
    if node is None:
        return ''
    encontrado = node.find(path, SEFAZ_NS)
    if encontrado is None or encontrado.text is None:
        return ''
    return encontrado.text.strip()


def _parse_decimal_xml(valor):
    if not valor:
        return Decimal('0')
    valor_normalizado = str(valor).strip().replace('.', '').replace(',', '.')
    if '.' in str(valor).strip():
        valor_normalizado = str(valor).strip().replace(',', '.')
    try:
        return Decimal(valor_normalizado)
    except Exception:
        return Decimal('0')


def _normalizar_empresa_minuta(filial):
    empresa = (filial or '').strip()
    if not empresa:
        return 'BRIDA LUBRIFICANTES LTDA'
    if ' - ' in empresa:
        prefixo, nome = empresa.split(' - ', 1)
        if prefixo.strip().isdigit() and nome.strip():
            return nome.strip()
    return empresa


def _formatar_veiculo_minuta(romaneio):
    veiculo = (romaneio.veiculo or '').strip()
    placa = (romaneio.placa or '').strip()
    if veiculo and placa and placa not in veiculo:
        return f'{veiculo} / {placa}'
    return veiculo or placa or '-'


def _resolver_relacoes_minuta_pdf(itens):
    numeros_nota = {item.numero_nota for item in itens if item.numero_nota}
    nf_ids = {item.nf_id for item in itens if item.nf_id}
    nfs_por_id = {}
    nfs_por_numero = {}

    if numeros_nota or nf_ids:
        notas = (
            NotaFiscal.objects.filter(Q(id__in=nf_ids) | Q(numero__in=numeros_nota))
            .select_related('rota', 'cliente')
            .prefetch_related(Prefetch('itens', queryset=NotaFiscalItem.objects.select_related('produto').order_by('id')))
            .order_by('numero', '-data_emissao', '-id')
        )
        for nota in notas:
            nfs_por_id[nota.id] = nota
            nfs_por_numero.setdefault(nota.numero, nota)

    entradas_por_numero = {}
    entradas = (
        EntradaNF.objects.filter(numero_nf__in=numeros_nota)
        .exclude(numero_nf='')
        .order_by('numero_nf', '-data_importacao', '-id')
    )
    for entrada in entradas:
        entradas_por_numero.setdefault(entrada.numero_nf, entrada)

    return nfs_por_id, nfs_por_numero, entradas_por_numero


def _resolver_nf_minuta_item(item, nfs_por_id, nfs_por_numero):
    if item.nf_id and item.nf_id in nfs_por_id:
        return nfs_por_id[item.nf_id]
    return nfs_por_numero.get(item.numero_nota)


def _extrair_rota_xml(informacoes_complementares):
    if not informacoes_complementares:
        return ''
    correspondencia = re.search(r'Rota:\s*([^\n\r]+)', informacoes_complementares, flags=re.IGNORECASE)
    if not correspondencia:
        return ''
    return _limpar_nome_rota_minuta(correspondencia.group(1))


def _limpar_nome_rota_minuta(valor):
    if not valor:
        return ''

    rota = str(valor).replace('\\N', '\n').replace('\\n', '\n')
    rota = re.sub(r'(?i)^\s*rota\s*:\s*', '', rota)
    rota = re.split(r'[\n\r]', rota, maxsplit=1)[0]
    rota = re.split(
        r'(?i)\b(?:trib\s*aprox(?:\.|imado)?|valor\s+cbs|valor\s+ibs|ibpt|credito\s+de\s+icms|impostos?|fed(?:eral)?|est(?:adual)?|mun(?:icipal)?|fonte)\b',
        rota,
        maxsplit=1,
    )[0]
    rota = re.split(r'[|;]', rota, maxsplit=1)[0]
    rota = re.sub(r'\s+', ' ', rota).strip(' -|;\\/')
    return rota.upper()


def _quantidade_total_nf_minuta(itens_nf=None, itens_xml=None):
    if itens_nf:
        return sum((item.quantidade or Decimal('0')) for item in itens_nf)
    if itens_xml:
        return sum((item.get('quantidade') or Decimal('0')) for item in itens_xml)
    return Decimal('0')


def _carregar_itens_xml_minuta(entrada):
    if entrada is None:
        return None

    try:
        with open_entrada_xml(entrada) as stream:
            tree = ET.parse(stream)
    except (XMLStorageUnavailableError, ET.ParseError, FileNotFoundError, OSError, ValueError):
        return None

    root = tree.getroot()
    inf_nfe = root.find('.//nfe:infNFe', SEFAZ_NS)
    if inf_nfe is None:
        return None

    informacoes_complementares = _texto_xml(inf_nfe, './/nfe:infAdic/nfe:infCpl')
    itens = []
    for det in inf_nfe.findall('.//nfe:det', SEFAZ_NS):
        codigo = _texto_xml(det, './nfe:prod/nfe:cProd')
        descricao = _texto_xml(det, './nfe:prod/nfe:xProd')
        unidade = _texto_xml(det, './nfe:prod/nfe:uCom') or 'UN'
        quantidade = _parse_decimal_xml(_texto_xml(det, './nfe:prod/nfe:qCom'))
        if not codigo and not descricao:
            continue
        itens.append(
            {
                'codigo': codigo or '-',
                'descricao': descricao or codigo or 'Produto sem descricao',
                'unidade': unidade,
                'quantidade': quantidade,
            }
        )

    return {
        'rota': _extrair_rota_xml(informacoes_complementares),
        'itens': itens,
    }


def _nome_rota_minuta(item, nf, xml_data=None):
    if nf and nf.rota_id:
        return _limpar_nome_rota_minuta(str(nf.rota))
    if xml_data and xml_data.get('rota'):
        return xml_data['rota']
    if item.romaneio.rotas:
        return _limpar_nome_rota_minuta(item.romaneio.rotas)
    if item.bairro:
        return _limpar_nome_rota_minuta(item.bairro)
    return 'NAO DEFINIDA'


def _nome_cliente_minuta(item, nf):
    return item.razao_social or item.fantasia or (nf.cliente.nome if nf else '-')


def _peso_item_minuta(item_nf, itens_nf, peso_total_nf):
    peso_total = peso_total_nf or Decimal('0')
    if not itens_nf:
        return Decimal('0')

    quantidade_total = sum((item.quantidade or Decimal('0')) for item in itens_nf)
    if quantidade_total > 0:
        return (peso_total * (item_nf.quantidade or Decimal('0')) / quantidade_total).quantize(Decimal('0.01'))

    return (peso_total / Decimal(len(itens_nf))).quantize(Decimal('0.01'))


def _linha_produto_minuta(item_nf, itens_nf, peso_total_nf):
    codigo = item_nf.codigo_operacional or '-'
    descricao = item_nf.descricao_operacional or 'Produto sem descricao'
    unidade = (
        (item_nf.produto.unidade if item_nf.produto_id else '')
        or (item_nf.produto.embalagem if item_nf.produto_id else '')
        or 'UN'
    )
    quantidade = _formatar_quantidade_pdf(item_nf.quantidade or Decimal('0'))
    peso_item = _formatar_decimal_pdf(_peso_item_minuta(item_nf, itens_nf, peso_total_nf), 2)
    return {
        'descricao': f'PI: {descricao}',
        'codigo': codigo,
        'quantidade': quantidade,
        'unidade': unidade,
        'peso': peso_item,
    }


def _peso_item_xml_minuta(item_xml, itens_xml, peso_total_nf):
    peso_total = peso_total_nf or Decimal('0')
    if not itens_xml:
        return Decimal('0')
    quantidade_total = sum((item.get('quantidade') or Decimal('0')) for item in itens_xml)
    if quantidade_total > 0:
        return (peso_total * (item_xml.get('quantidade') or Decimal('0')) / quantidade_total).quantize(Decimal('0.01'))
    return (peso_total / Decimal(len(itens_xml))).quantize(Decimal('0.01'))


def _linha_produto_xml_minuta(item_xml, itens_xml, peso_total_nf):
    quantidade = _formatar_quantidade_pdf(item_xml.get('quantidade') or Decimal('0'))
    peso_item = _formatar_decimal_pdf(_peso_item_xml_minuta(item_xml, itens_xml, peso_total_nf), 2)
    return {
        'descricao': f"PI: {item_xml.get('descricao') or 'Produto sem descricao'}",
        'codigo': item_xml.get('codigo') or '-',
        'quantidade': quantidade,
        'unidade': item_xml.get('unidade') or 'UN',
        'peso': peso_item,
    }


def _nome_exportacao_minuta(itens, fallback='lote-ativo'):
    codigos = {item.romaneio.codigo_romaneio for item in itens if getattr(item, 'romaneio', None)}
    if len(codigos) == 1:
        return next(iter(codigos))
    return fallback or 'lote-ativo'


def _cidade_entrega_minuta(item):
    cidade = (item.bairro or nota_fiscal_bairro_valor(getattr(item, 'nf', None)) or '').strip()
    return cidade or '-'


def _uf_entrega_minuta(item):
    return 'SP'


def _desenhar_numero_pagina_entrega(canvas, doc):
    canvas.saveState()
    canvas.setFont('Helvetica', 9)
    canvas.setFillColor(colors.HexColor('#64748b'))
    largura_pagina, altura_pagina = doc.pagesize
    canvas.drawRightString(largura_pagina - doc.rightMargin, altura_pagina - 12 * mm, f'Pagina: {canvas.getPageNumber()}')
    canvas.restoreState()


def _build_minuta_entrega_pdf(itens_queryset):
    itens = list(itens_queryset)
    if not itens:
        raise MinutaImportacaoError('Nenhum item da minuta está disponível para gerar o PDF.')

    fontes = _registrar_fontes_minuta()
    romaneios_agrupados = []
    grupos = defaultdict(list)
    for item in sorted(itens, key=lambda registro: ((registro.romaneio.data_saida or timezone.now().date()), registro.romaneio.codigo_romaneio, registro.id or 0)):
        grupos[item.romaneio_id].append(item)
    for romaneio_id, itens_romaneio in grupos.items():
        romaneios_agrupados.append({'romaneio': itens_romaneio[0].romaneio, 'itens': itens_romaneio})

    buffer = BytesIO()
    pagesize_entrega = landscape(A4)
    document = SimpleDocTemplate(
        buffer,
        pagesize=pagesize_entrega,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        pageCompression=0,
    )
    largura_util = document.width
    largura_header = [largura_util * 0.58, largura_util * 0.42]
    largura_meta = [largura_util * 0.28, largura_util * 0.3, largura_util * 0.42]
    largura_info = [largura_util * 0.14, largura_util * 0.34, largura_util * 0.14, largura_util * 0.38]
    largura_tabela = [largura_util * 0.11, largura_util * 0.12, largura_util * 0.36, largura_util * 0.18, largura_util * 0.06, largura_util * 0.08, largura_util * 0.09]
    largura_totais = [largura_util * 0.27, largura_util * 0.31, largura_util * 0.42]
    largura_bloco_entrega = [largura_util * 0.4, largura_util * 0.27, largura_util * 0.33]
    largura_assinaturas = [largura_util * 0.5, largura_util * 0.5]
    styles = getSampleStyleSheet()
    empresa_style = ParagraphStyle('MinutaEntregaEmpresa', parent=styles['Normal'], fontName=fontes['bold'], fontSize=15, leading=17.5)
    titulo_style = ParagraphStyle('MinutaEntregaTitulo', parent=styles['Normal'], fontName=fontes['bold'], fontSize=15, leading=17.5, alignment=2)
    meta_style = ParagraphStyle('MinutaEntregaMeta', parent=styles['Normal'], fontName=fontes['regular'], fontSize=9.8, leading=12, textColor='#667085')
    meta_label_style = ParagraphStyle('MinutaEntregaMetaLabel', parent=meta_style, fontName=fontes['bold'], textColor='#111827')
    tabela_header_style = ParagraphStyle('MinutaEntregaTabelaHeader', parent=styles['Normal'], fontName=fontes['bold'], fontSize=9.8, leading=11.4)
    tabela_cell_style = ParagraphStyle('MinutaEntregaTabelaCell', parent=styles['Normal'], fontName=fontes['regular'], fontSize=9.7, leading=11.6)
    tabela_cell_bold_style = ParagraphStyle('MinutaEntregaTabelaCellBold', parent=tabela_cell_style, fontName=fontes['bold'])
    resumo_label_style = ParagraphStyle('MinutaEntregaResumoLabel', parent=styles['Normal'], fontName=fontes['bold'], fontSize=9.8, leading=11.8)
    resumo_valor_style = ParagraphStyle('MinutaEntregaResumoValor', parent=styles['Normal'], fontName=fontes['bold'], fontSize=10.2, leading=12)
    secao_style = ParagraphStyle('MinutaEntregaSecao', parent=styles['Normal'], fontName=fontes['bold'], fontSize=10.5, leading=12.6)
    rodape_style = ParagraphStyle('MinutaEntregaRodape', parent=styles['Normal'], fontName=fontes['regular'], fontSize=8.9, leading=11.2, textColor='#6b7280')
    assinatura_style = ParagraphStyle('MinutaEntregaAssinatura', parent=styles['Normal'], fontName=fontes['regular'], fontSize=8.7, leading=10.4, alignment=1)

    elementos = []
    data_hora = timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M:%S')

    for indice_romaneio, grupo in enumerate(romaneios_agrupados):
        romaneio = grupo['romaneio']
        itens_romaneio = sorted(grupo['itens'], key=lambda item: item.id or 0)
        total_peso = sum((item.peso_kg or Decimal('0')) for item in itens_romaneio)
        total_valor = sum((item.valor_total or Decimal('0')) for item in itens_romaneio)
        total_nfs = len(itens_romaneio)

        header_top = Table(
            [[Paragraph(_normalizar_empresa_minuta(romaneio.filial).upper(), empresa_style), Paragraph('MINUTA DE ENTREGA', titulo_style)]],
            colWidths=largura_header,
            hAlign='LEFT',
        )
        header_top.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')
        ]))
        elementos.append(header_top)
        elementos.append(Spacer(1, 4 * mm))

        meta_top = Table(
            [[
                Paragraph(f'<b>Emissao:</b> {data_hora}', meta_style),
                Paragraph(f'<b>Carregamento:</b> {romaneio.codigo_romaneio}', meta_style),
                Paragraph('', meta_style),
            ]],
            colWidths=largura_meta,
            hAlign='LEFT',
        )
        meta_top.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)
        ]))
        elementos.append(meta_top)
        elementos.append(Spacer(1, 3 * mm))
        elementos.append(HRFlowable(width='100%', thickness=0.8, color='#d2d6dc', spaceBefore=0, spaceAfter=4 * mm))

        info_box = Table(
            [[
                Paragraph(f'<b>Transportadora</b>', meta_label_style), Paragraph(romaneio.transportadora or '-', meta_style),
                Paragraph(f'<b>Veiculo</b>', meta_label_style), Paragraph(_formatar_veiculo_minuta(romaneio), meta_style),
            ], [
                Paragraph(f'<b>Placa</b>', meta_label_style), Paragraph(romaneio.placa or _formatar_veiculo_minuta(romaneio), meta_style),
                Paragraph(f'<b>Motorista</b>', meta_label_style), Paragraph(romaneio.motorista or '-', meta_style),
            ]],
            colWidths=largura_info,
            hAlign='LEFT',
        )
        info_box.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.white), ('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor('#d2d6dc')),
            ('ROUNDEDCORNERS', [10, 10, 10, 10]), ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10), ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')
        ]))
        elementos.append(info_box)
        elementos.append(Spacer(1, 5 * mm))

        tabela_rows = [[
            Paragraph('NF', tabela_header_style),
            Paragraph('Emissao', tabela_header_style),
            Paragraph('Cliente', tabela_header_style),
            Paragraph('Cidade', tabela_header_style),
            Paragraph('UF', tabela_header_style),
            Paragraph('Peso', tabela_header_style),
            Paragraph('Valor', tabela_header_style),
        ]]
        for item in itens_romaneio:
            data_emissao = item.nf.data_emissao.strftime('%d/%m/%Y') if item.nf and item.nf.data_emissao else (romaneio.data_saida.strftime('%d/%m/%Y') if romaneio.data_saida else '-')
            tabela_rows.append([
                Paragraph(item.numero_nota, tabela_cell_bold_style),
                Paragraph(data_emissao, tabela_cell_style),
                Paragraph(_nome_cliente_minuta(item, item.nf), tabela_cell_style),
                Paragraph(_cidade_entrega_minuta(item), tabela_cell_style),
                Paragraph(_uf_entrega_minuta(item), tabela_cell_style),
                Paragraph(_formatar_decimal_pdf(item.peso_kg or Decimal('0'), 2), tabela_cell_style),
                Paragraph(_formatar_moeda_pdf(item.valor_total or Decimal('0')), tabela_cell_style),
            ])

        tabela_principal = Table(
            tabela_rows,
            colWidths=largura_tabela,
            repeatRows=1,
            hAlign='LEFT',
        )
        tabela_principal.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eceff3')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#111827')),
            ('LINEBELOW', (0, 0), (-1, 0), 0.6, colors.HexColor('#d7dce3')),
            ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8), ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 1), (-1, -1), 0.35, colors.HexColor('#e5e7eb')),
        ]))
        elementos.append(tabela_principal)
        elementos.append(Spacer(1, 4 * mm))

        tabela_total = Table(
            [[
                Paragraph(f'Total de NFs: {total_nfs}', resumo_valor_style),
                Paragraph(f'Total Peso: {_formatar_decimal_pdf(total_peso, 2)}', resumo_valor_style),
                Paragraph(f'Total Valor: {_formatar_moeda_pdf(total_valor)}', resumo_valor_style),
            ]],
            colWidths=largura_totais,
            hAlign='LEFT',
        )
        tabela_total.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#eef2f7')), ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10), ('TOPPADDING', (0, 0), (-1, -1), 10), ('BOTTOMPADDING', (0, 0), (-1, -1), 10), ('ROUNDEDCORNERS', [10, 10, 10, 10])
        ]))
        elementos.append(tabela_total)
        elementos.append(Spacer(1, 10 * mm))

        bloco_entrega = Table(
            [[
                Paragraph('MERCADORIA ENTREGUE EM: ____/____/________', secao_style),
                Paragraph('HORA DA ENTRADA: ____:____', secao_style),
                Paragraph('HORA DA SAIDA: ____:____', secao_style),
            ]],
            colWidths=largura_bloco_entrega,
            hAlign='LEFT',
        )
        bloco_entrega.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 8), ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)
        ]))
        elementos.append(bloco_entrega)
        elementos.append(Spacer(1, 8 * mm))
        elementos.append(Paragraph('OBS: O PAGAMENTO DO VALOR DO FRETE NO PRAZO COMBINADO ESTARA AUTOMATICAMENTE VINCULADO AO RETORNO DO CANHOTO DA NOTA FISCAL E CABECALHO DO BOLETO BANCARIO DEVIDAMENTE ASSINADO E CARIMBADO PELO CLIENTE.', rodape_style))
        elementos.append(Spacer(1, 14 * mm))

        assinaturas = Table(
            [[Paragraph('________________________________________<br/>Transportador', assinatura_style), Paragraph('________________________________________<br/>Cliente / Carimbo', assinatura_style)]],
            colWidths=largura_assinaturas,
            hAlign='LEFT',
        )
        assinaturas.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)
        ]))
        elementos.append(assinaturas)
        elementos.append(Spacer(1, 4 * mm))
        elementos.append(Paragraph('DECLARO ESTAR RETIRANDO AS MERCADORIAS REFERENTES AS NOTAS FISCAIS CONSTANTES NESTE DOCUMENTO EM PERFEITO ESTADO.', rodape_style))

        if indice_romaneio < len(romaneios_agrupados) - 1:
            elementos.append(PageBreak())

    document.build(elementos, onFirstPage=_desenhar_numero_pagina_entrega, onLaterPages=_desenhar_numero_pagina_entrega)
    return buffer.getvalue()


def _build_minuta_romaneio_pdf(itens_queryset):
    itens = list(itens_queryset)
    if not itens:
        raise MinutaImportacaoError('Nenhum item da minuta está disponível para gerar o PDF.')

    nfs_por_id, nfs_por_numero, entradas_por_numero = _resolver_relacoes_minuta_pdf(itens)
    xml_cache = {}

    romaneios = []
    romaneio_atual = None
    romaneio_id_atual = None
    for item in itens:
        if item.romaneio_id != romaneio_id_atual:
            romaneio_atual = {'romaneio': item.romaneio, 'itens': []}
            romaneios.append(romaneio_atual)
            romaneio_id_atual = item.romaneio_id
        romaneio_atual['itens'].append(item)

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        pageCompression=0,
    )
    styles = getSampleStyleSheet()
    fontes = _registrar_fontes_minuta()
    titulo_style = ParagraphStyle(
        'MinutaRomaneioTitulo',
        parent=styles['Heading1'],
        fontName=fontes['bold'],
        fontSize=23,
        leading=26,
        alignment=1,
        spaceAfter=6,
    )
    empresa_style = ParagraphStyle(
        'MinutaRomaneioEmpresa',
        parent=styles['Heading2'],
        fontName=fontes['bold'],
        fontSize=15.5,
        leading=18,
        alignment=1,
        spaceAfter=12,
    )
    cabecalho_style = ParagraphStyle(
        'MinutaRomaneioCabecalho',
        parent=styles['Normal'],
        fontName=fontes['regular'],
        fontSize=11,
        leading=14,
    )
    cabecalho_label_style = ParagraphStyle(
        'MinutaRomaneioCabecalhoLabel',
        parent=cabecalho_style,
        fontName=fontes['bold'],
    )
    rota_style = ParagraphStyle(
        'MinutaRomaneioRota',
        parent=styles['Heading2'],
        fontName=fontes['bold'],
        fontSize=12.4,
        leading=15,
        spaceBefore=7,
        spaceAfter=6,
        textColor='#1f3b64',
    )
    nf_style = ParagraphStyle(
        'MinutaRomaneioNF',
        parent=styles['Normal'],
        fontName=fontes['bold'],
        fontSize=11.2,
        leading=14,
    )
    cliente_style = ParagraphStyle(
        'MinutaRomaneioCliente',
        parent=styles['Normal'],
        fontName=fontes['regular'],
        fontSize=10.9,
        leading=13.5,
    )
    meta_nf_style = ParagraphStyle(
        'MinutaRomaneioMetaNF',
        parent=styles['Normal'],
        fontName=fontes['regular'],
        fontSize=10.7,
        leading=13,
    )
    produto_linha_style = ParagraphStyle(
        'MinutaRomaneioProdutoLinha',
        parent=styles['Normal'],
        fontName=fontes['regular'],
        fontSize=10.1,
        leading=12.6,
    )
    produto_titulo_style = ParagraphStyle(
        'MinutaRomaneioProdutoTitulo',
        parent=styles['Normal'],
        fontName=fontes['bold'],
        fontSize=10.7,
        leading=13,
        textColor='#111827',
    )
    produto_valor_style = ParagraphStyle(
        'MinutaRomaneioProdutoValor',
        parent=styles['Normal'],
        fontName=fontes['regular'],
        fontSize=10,
        leading=12,
        alignment=1,
    )
    aviso_style = ParagraphStyle(
        'MinutaRomaneioAviso',
        parent=produto_linha_style,
        textColor='#7c2d12',
        spaceAfter=3,
    )
    resumo_style = ParagraphStyle(
        'MinutaRomaneioResumo',
        parent=styles['Normal'],
        fontName=fontes['bold'],
        fontSize=14,
        leading=17,
        spaceBefore=7,
        spaceAfter=6,
    )
    resumo_label_style = ParagraphStyle(
        'MinutaRomaneioResumoLabel',
        parent=styles['Normal'],
        fontName=fontes['bold'],
        fontSize=9.8,
        leading=12,
        textColor='#324968',
        alignment=1,
    )
    resumo_valor_style = ParagraphStyle(
        'MinutaRomaneioResumoValor',
        parent=styles['Normal'],
        fontName=fontes['bold'],
        fontSize=14,
        leading=17,
        alignment=1,
        textColor='#0f172a',
    )
    assinatura_style = ParagraphStyle(
        'MinutaRomaneioAssinatura',
        parent=styles['Normal'],
        fontName=fontes['bold'],
        fontSize=9.5,
        leading=11,
        alignment=1,
    )

    data_hora = timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M')
    elementos = []
    largura_tabela = [30 * mm, 33 * mm, 84 * mm, 16 * mm, 21 * mm]
    largura_produtos = [132 * mm, 13 * mm, 12 * mm, 21 * mm]

    romaneios_render = sorted(
        romaneios,
        key=lambda grupo: max((item.id or 0) for item in grupo['itens']) if grupo['itens'] else 0,
        reverse=True,
    )

    for indice_romaneio, grupo in enumerate(romaneios_render):
        romaneio = grupo['romaneio']
        itens_romaneio = sorted(grupo['itens'], key=lambda item: item.id or 0, reverse=True)
        total_peso = sum((item.peso_kg or Decimal('0')) for item in itens_romaneio)
        total_volume = sum((item.volume_m3 or Decimal('0')) for item in itens_romaneio)
        quantidade_nf = len({item.numero_nota for item in itens_romaneio})
        itens_por_rota = defaultdict(list)
        for item in itens_romaneio:
            nf = _resolver_nf_minuta_item(item, nfs_por_id, nfs_por_numero)
            entrada = entradas_por_numero.get(item.numero_nota)
            xml_data = None
            if entrada:
                xml_data = xml_cache.setdefault(entrada.numero_nf, _carregar_itens_xml_minuta(entrada))
            rota_nome = _nome_rota_minuta(item, nf, xml_data=xml_data)
            itens_por_rota[rota_nome].append((item, nf, xml_data, entrada))

        elementos.append(Paragraph('MINUTA DE CARREGAMENTO', titulo_style))
        elementos.append(Paragraph(_normalizar_empresa_minuta(romaneio.filial).upper(), empresa_style))
        tabela_topo = Table(
            [
                [
                    Paragraph(f'<b>Carregamento:</b> {romaneio.codigo_romaneio}', cabecalho_style),
                    Paragraph(f'<b>Emissao:</b> {data_hora}', cabecalho_style),
                ],
                [
                    Paragraph(f'<b>Data:</b> {romaneio.data_saida.strftime("%d/%m/%Y") if romaneio.data_saida else "-"}', cabecalho_style),
                    Paragraph('', cabecalho_style),
                ],
            ],
            colWidths=[83 * mm, 79 * mm],
            hAlign='LEFT',
        )
        tabela_topo.setStyle(
            TableStyle(
                [
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 0),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                    ('TOPPADDING', (0, 0), (-1, -1), 0),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ]
            )
        )
        elementos.append(tabela_topo)
        elementos.append(Spacer(1, 3 * mm))
        elementos.append(HRFlowable(width='98%', thickness=0.85, color='#c9d1db', spaceBefore=0, spaceAfter=4.5 * mm, hAlign='CENTER'))

        tabela_operacional = Table(
            [
                [Paragraph('<b>TRANSPORTADORA:</b>', cabecalho_style), Paragraph(romaneio.transportadora or '-', cabecalho_style)],
                [Paragraph('<b>VEICULO:</b>', cabecalho_style), Paragraph(_formatar_veiculo_minuta(romaneio), cabecalho_style)],
                [Paragraph('<b>MOTORISTA:</b>', cabecalho_style), Paragraph(romaneio.motorista or '-', cabecalho_style)],
            ],
            colWidths=[35 * mm, 127 * mm],
            hAlign='LEFT',
        )
        tabela_operacional.setStyle(
            TableStyle(
                [
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 0),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                    ('TOPPADDING', (0, 0), (-1, -1), 2.2),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 2.2),
                ]
            )
        )
        elementos.append(tabela_operacional)
        elementos.append(Spacer(1, 4 * mm))
        elementos.append(HRFlowable(width='98%', thickness=1, color='#b9c1cc', spaceBefore=0, spaceAfter=4.5 * mm, dash=(4, 3), hAlign='CENTER'))

        tabela_colunas = Table(
            [[
                Paragraph('Nota', cabecalho_label_style),
                Paragraph('Emissao', cabecalho_label_style),
                Paragraph('Cliente', cabecalho_label_style),
                Paragraph('Qtd', cabecalho_label_style),
                Paragraph('Peso', cabecalho_label_style),
            ]],
            colWidths=largura_tabela,
            hAlign='LEFT',
        )
        tabela_colunas.setStyle(
            TableStyle(
                [
                    ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#eef2f7')),
                    ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#18212f')),
                    ('LEFTPADDING', (0, 0), (-1, -1), 10),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                    ('TOPPADDING', (0, 0), (-1, -1), 8),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                    ('LINEBELOW', (0, 0), (-1, -1), 0.55, colors.HexColor('#d1d9e4')),
                    ('ROUNDRECT', (0, 0), (-1, -1), 4, colors.HexColor('#f2f4f7')),
                ]
            )
        )
        elementos.append(tabela_colunas)
        elementos.append(Spacer(1, 5 * mm))

        for rota_nome in itens_por_rota:
            elementos.append(HRFlowable(width='100%', thickness=1.9, color='#1d3557', spaceBefore=0, spaceAfter=4 * mm))
            elementos.append(Paragraph(f'ROTA: {rota_nome}', rota_style))
            for indice_nf, (item, nf, xml_data, entrada) in enumerate(itens_por_rota[rota_nome]):
                cliente_nome = _nome_cliente_minuta(item, nf)
                data_nf = nf.data_emissao.strftime('%d/%m/%Y') if nf and nf.data_emissao else (romaneio.data_saida.strftime('%d/%m/%Y') if romaneio.data_saida else '-')
                itens_nf = list(nf.itens.all()) if nf else []
                itens_xml = xml_data.get('itens') if xml_data else []
                quantidade_total_nf = _quantidade_total_nf_minuta(itens_nf=itens_nf, itens_xml=itens_xml)
                tabela_nf = Table(
                    [[
                        Paragraph(item.numero_nota, nf_style),
                        Paragraph(data_nf, meta_nf_style),
                        Paragraph(cliente_nome, cliente_style),
                        Paragraph(_formatar_quantidade_pdf(quantidade_total_nf), meta_nf_style),
                        Paragraph(_formatar_decimal_pdf(item.peso_kg or Decimal('0'), 2), meta_nf_style),
                    ]],
                    colWidths=largura_tabela,
                    hAlign='LEFT',
                )
                tabela_nf.setStyle(
                    TableStyle(
                        [
                            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                            ('LEFTPADDING', (0, 0), (-1, -1), 5),
                            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                            ('TOPPADDING', (0, 0), (-1, -1), 2.6),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 2.6),
                        ]
                    )
                )
                bloco_nf = [
                    tabela_nf,
                    Spacer(1, 2.2 * mm),
                    Paragraph('• Produtos:', produto_titulo_style),
                    Spacer(1, 1 * mm),
                ]

                if itens_nf:
                    for item_nf in reversed(itens_nf):
                        produto = _linha_produto_minuta(item_nf, itens_nf, item.peso_kg or Decimal('0'))
                        tabela_produto = Table(
                            [[
                                Paragraph(f'• {produto["descricao"]} - ({produto["codigo"]})', produto_linha_style),
                                Paragraph(produto['quantidade'], produto_valor_style),
                                Paragraph(produto['unidade'], produto_valor_style),
                                Paragraph(produto['peso'], produto_valor_style),
                            ]],
                            colWidths=largura_produtos,
                            hAlign='LEFT',
                        )
                        tabela_produto.setStyle(
                            TableStyle(
                                [
                                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                    ('LEFTPADDING', (0, 0), (0, -1), 18),
                                    ('RIGHTPADDING', (0, 0), (0, -1), 8),
                                    ('LEFTPADDING', (1, 0), (-1, -1), 2),
                                    ('RIGHTPADDING', (1, 0), (-1, -1), 2),
                                    ('TOPPADDING', (0, 0), (-1, -1), 1.6),
                                    ('BOTTOMPADDING', (0, 0), (-1, -1), 2.2),
                                ]
                            )
                        )
                        bloco_nf.append(tabela_produto)
                        bloco_nf.append(Spacer(1, 0.8 * mm))
                elif itens_xml:
                    for item_xml in reversed(itens_xml):
                        produto = _linha_produto_xml_minuta(item_xml, itens_xml, item.peso_kg or Decimal('0'))
                        tabela_produto = Table(
                            [[
                                Paragraph(f'• {produto["descricao"]} - ({produto["codigo"]})', produto_linha_style),
                                Paragraph(produto['quantidade'], produto_valor_style),
                                Paragraph(produto['unidade'], produto_valor_style),
                                Paragraph(produto['peso'], produto_valor_style),
                            ]],
                            colWidths=largura_produtos,
                            hAlign='LEFT',
                        )
                        tabela_produto.setStyle(
                            TableStyle(
                                [
                                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                    ('LEFTPADDING', (0, 0), (0, -1), 18),
                                    ('RIGHTPADDING', (0, 0), (0, -1), 8),
                                    ('LEFTPADDING', (1, 0), (-1, -1), 2),
                                    ('RIGHTPADDING', (1, 0), (-1, -1), 2),
                                    ('TOPPADDING', (0, 0), (-1, -1), 1.6),
                                    ('BOTTOMPADDING', (0, 0), (-1, -1), 2.2),
                                ]
                            )
                        )
                        bloco_nf.append(tabela_produto)
                        bloco_nf.append(Spacer(1, 0.8 * mm))
                else:
                    aviso_xml = 'XML localizado sem itens processados' if entrada else 'XML nao localizado'
                    bloco_nf.append(Paragraph(aviso_xml, aviso_style))

                if indice_nf < len(itens_por_rota[rota_nome]) - 1:
                    bloco_nf.append(Spacer(1, 2.4 * mm))
                    bloco_nf.append(HRFlowable(width='100%', thickness=0.9, color='#334155', spaceBefore=0, spaceAfter=4.2 * mm))
                else:
                    bloco_nf.append(Spacer(1, 4.8 * mm))

                elementos.append(KeepTogether(bloco_nf))

        quantidade_total_romaneio = sum(
            _quantidade_total_nf_minuta(
                itens_nf=list(_resolver_nf_minuta_item(item, nfs_por_id, nfs_por_numero).itens.all()) if _resolver_nf_minuta_item(item, nfs_por_id, nfs_por_numero) else None,
                itens_xml=(xml_cache.get(item.numero_nota) or {}).get('itens') if item.numero_nota in xml_cache else None,
            )
            for item in itens_romaneio
        )
        tabela_total = Table(
            [[
                Paragraph('TOTAL GERAL', resumo_style),
                Paragraph('NF(s):', resumo_label_style),
                Paragraph('QTD TOTAL:', resumo_label_style),
                Paragraph('PESO TOTAL (KG):', resumo_label_style),
            ], [
                Paragraph('', resumo_label_style),
                Paragraph(str(quantidade_nf), resumo_valor_style),
                Paragraph(_formatar_quantidade_pdf(quantidade_total_romaneio), resumo_valor_style),
                Paragraph(_formatar_decimal_pdf(total_peso, 2), resumo_valor_style),
            ]],
            colWidths=[48 * mm, 28 * mm, 49 * mm, 49 * mm],
            hAlign='LEFT',
        )
        tabela_total.setStyle(
            TableStyle(
                [
                    ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
                    ('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor('#d2dae6')),
                    ('INNERGRID', (1, 0), (-1, -1), 0.6, colors.HexColor('#d2dae6')),
                    ('SPAN', (0, 0), (0, 1)),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('ALIGN', (0, 0), (0, 1), 'LEFT'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 10),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                    ('TOPPADDING', (0, 0), (-1, -1), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
                ]
            )
        )
        elementos.append(tabela_total)
        elementos.append(Spacer(1, 4 * mm))

        elementos.append(Spacer(1, 5 * mm))
        elementos.append(Paragraph('________________________________________', assinatura_style))
        elementos.append(Spacer(1, 2 * mm))
        elementos.append(Paragraph('ASS. CONFERENTE', assinatura_style))
        if indice_romaneio < len(romaneios_render) - 1:
            elementos.append(PageBreak())

    document.build(elementos)
    return buffer.getvalue()


class HealthCheckView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({'status': 'ok'}, status=status.HTTP_200_OK)


@require_profiles(Usuario.Perfil.GESTOR)
def home(request):
    context = {
        'usuario': request.user,
        'modulos_operacionais': [
            'Separacao',
            'Conferencia',
            'Controle operacional',
            'Gestao de XML',
            'Controle de setores',
            'Gestao logistica',
        ],
    }
    context.update(build_access_context(request.user))
    return render(request, 'home.html', context)


@require_profiles(Usuario.Perfil.GESTOR)
def minuta(request):
    if request.method == 'POST':
        acao = (request.POST.get('acao') or '').strip()
        if acao == 'cancelar_preview':
            request.session.pop(MINUTA_PREVIEW_SESSION_KEY, None)
            messages.info(request, 'Prévia da importação da minuta descartada.')
            return redirect('web-minuta')

        if acao == 'confirmar_importacao':
            preview = request.session.get(MINUTA_PREVIEW_SESSION_KEY)
            if not preview:
                messages.error(request, 'Nenhuma prévia de importação está disponível para confirmação.')
                return redirect('web-minuta')
            try:
                resultado = confirmar_importacao_minuta(preview, request.user)
            except MinutaImportacaoError as exc:
                logger.error('ERRO REAL MINUTA: confirmacao_negocio user_id=%s erro=%s', getattr(request.user, 'id', None), str(exc))
                messages.error(request, str(exc))
                return redirect('web-minuta')
            except Exception as exc:
                traceback.print_exc()
                logger.exception('ERRO REAL MINUTA: confirmacao_inesperada user_id=%s erro=%s', getattr(request.user, 'id', None), str(exc))
                messages.error(request, f'ERRO REAL: {str(exc)}')
                raise
            request.session.pop(MINUTA_PREVIEW_SESSION_KEY, None)
            messages.success(
                request,
                f"Importação da minuta concluída: {resultado['romaneios']} romaneio(s), {resultado['itens']} NF(s) e {resultado['duplicados']} duplicidade(s) sinalizada(s).",
            )
            return redirect('web-minuta')

        if acao == 'upload':
            arquivo = request.FILES.get('arquivo')
            if arquivo is None:
                messages.error(request, 'Selecione a planilha de romaneio para importação.')
                return redirect('web-minuta')
            try:
                preview = montar_preview_importacao_minuta(arquivo, request.user)
            except MinutaImportacaoError as exc:
                logger.error('ERRO REAL MINUTA: preview_negocio user_id=%s erro=%s', getattr(request.user, 'id', None), str(exc))
                messages.error(request, str(exc))
                return redirect('web-minuta')

            try:
                resultado = confirmar_importacao_minuta(preview, request.user, validar_restricoes=False)
            except MinutaImportacaoError as exc:
                logger.error('ERRO REAL MINUTA: upload_negocio user_id=%s erro=%s', getattr(request.user, 'id', None), str(exc))
                messages.error(request, str(exc))
                return redirect('web-minuta')
            except Exception as exc:
                traceback.print_exc()
                logger.exception('ERRO REAL MINUTA: upload_inesperado user_id=%s erro=%s', getattr(request.user, 'id', None), str(exc))
                messages.error(request, f'ERRO REAL: {str(exc)}')
                raise
            request.session.pop(MINUTA_PREVIEW_SESSION_KEY, None)

            if preview['resumo'].get('bloqueadas'):
                messages.warning(
                    request,
                    f"A importação atual substituiu a versão anterior. Foram sinalizadas {preview['resumo']['bloqueadas']} NF(s) com restrição operacional ou fiscal; revise os status na listagem.",
                )
                messages.success(
                    request,
                    f"Importação da minuta concluída: {resultado['romaneios']} romaneio(s) e {resultado['itens']} NF(s) sincronizadas pela planilha atual.",
                )
                return redirect('web-minuta')

            if preview['resumo']['duplicados']:
                messages.warning(
                    request,
                    f"A importação atual substituiu a versão anterior. Foram identificadas {preview['resumo']['duplicados']} NF(s) também vinculadas a outro romaneio.",
                )
                messages.success(
                    request,
                    f"Importação da minuta concluída: {resultado['romaneios']} romaneio(s) e {resultado['itens']} NF(s) sincronizadas pela planilha atual.",
                )
                return redirect('web-minuta')

            messages.success(
                request,
                f"Importação da minuta concluída: {resultado['romaneios']} romaneio(s) e {resultado['itens']} NF(s) sincronizadas pela planilha atual.",
            )
            return redirect('web-minuta')

    filtros = {
        'romaneio': (request.GET.get('romaneio') or '').strip(),
        'status': (request.GET.get('status') or '').strip(),
        'busca': (request.GET.get('busca') or '').strip(),
    }
    linhas, resumo = listar_minuta_itens(**filtros)
    preview = request.session.get(MINUTA_PREVIEW_SESSION_KEY)
    context = {
        'usuario': request.user,
        'filtros': filtros,
        'resumo': resumo,
        'minuta_inconsistencias': get_minuta_inconsistencias(linhas),
        'linhas': preview['linhas'] if preview else linhas,
        'preview': preview,
        'status_opcoes': [
            'XML IMPORTADO',
            'AGUARDANDO LIBERACAO',
            'LIBERADA',
            'EM CONFERENCIA',
            'FINALIZADA',
            'FINALIZADA COM RESTRICAO',
            'LIBERADA COM RESTRICAO',
            'DUPLI',
            'NF COM PROBLEMA',
            'XML INVALIDO',
            'NF CANCELADA',
            'NF DENEGADA',
            'NF INCONSISTENTE',
            'NF BLOQUEADA',
            'NF INATIVA',
            'NF NÃO LOCALIZADA',
            'NF VINCULADA',
        ],
    }
    context.update(build_access_context(request.user))
    return render(request, 'minuta.html', context)


@require_profiles(Usuario.Perfil.GESTOR)
def minuta_pdf(request):
    logger.info('DEBUG MINUTA: gerando_pdf_inicio user_id=%s', getattr(request.user, 'id', None))
    filtros = {
        'romaneio': (request.GET.get('romaneio') or '').strip(),
        'status': (request.GET.get('status') or '').strip(),
        'busca': (request.GET.get('busca') or '').strip(),
    }
    gerar_carregamento = (request.GET.get('carregamento') or '1').strip() not in {'0', 'false', 'False'}
    gerar_entrega = (request.GET.get('entrega') or '').strip() in {'1', 'true', 'True'}
    if not gerar_carregamento and not gerar_entrega:
        messages.error(request, 'Selecione pelo menos um tipo de minuta para gerar o PDF.')
        return redirect('web-minuta')

    queryset = consultar_minuta_itens_queryset(**filtros)
    itens = list(queryset)
    inconsistencias = get_minuta_inconsistencias([
        {
            'status': item.status,
            'duplicado': item.duplicado,
        }
        for item in itens
    ])
    confirmacao_alertas = (request.GET.get('confirmar_alertas') or '').strip() in {'1', 'true', 'True'}
    if inconsistencias['possui_alertas'] and not confirmacao_alertas:
        messages.warning(
            request,
            'Foram encontradas inconsistências operacionais que podem impactar a geração do PDF.',
        )
        return redirect('web-minuta')
    nome_romaneio = _nome_exportacao_minuta(itens, fallback=filtros['romaneio'] or 'lote-ativo')
    try:
        arquivos = []
        if gerar_carregamento:
            logger.info('DEBUG MINUTA: gerando_pdf tipo=carregamento romaneio=%s itens=%s', nome_romaneio, len(itens))
            arquivos.append((f'minuta_carregamento_{nome_romaneio}.pdf', _build_minuta_romaneio_pdf(itens), 'application/pdf'))
        if gerar_entrega:
            logger.info('DEBUG MINUTA: gerando_pdf tipo=entrega romaneio=%s itens=%s', nome_romaneio, len(itens))
            arquivos.append((f'minuta_entrega_{nome_romaneio}.pdf', _build_minuta_entrega_pdf(itens), 'application/pdf'))
    except MinutaImportacaoError as exc:
        logger.error('ERRO REAL MINUTA: pdf_negocio user_id=%s erro=%s', getattr(request.user, 'id', None), str(exc))
        messages.error(request, str(exc))
        return redirect('web-minuta')
    except Exception as exc:
        traceback.print_exc()
        logger.exception('ERRO REAL MINUTA: pdf_inesperado user_id=%s erro=%s', getattr(request.user, 'id', None), str(exc))
        messages.error(request, f'ERRO REAL: {str(exc)}')
        raise

    if len(arquivos) == 1:
        nome_arquivo, conteudo, content_type = arquivos[0]
        response = HttpResponse(conteudo, content_type=content_type)
        response['Content-Disposition'] = f'inline; filename="{nome_arquivo}"'
        return response

    logger.info('DEBUG MINUTA: gerando_zip romaneio=%s arquivos=%s', nome_romaneio, len(arquivos))
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as zip_file:
        for nome_arquivo, conteudo, _content_type in arquivos:
            zip_file.writestr(nome_arquivo, conteudo)

    logger.info('DEBUG MINUTA: finalizando_importacao_pdf_zip romaneio=%s', nome_romaneio)
    response = HttpResponse(zip_buffer.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="minutas_{nome_romaneio}.zip"'
    return response
