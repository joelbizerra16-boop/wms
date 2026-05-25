"""Parser XML NFe para fluxo de recebimento (desacoplado do importador operacional)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import xml.etree.ElementTree as ET

SEFAZ_NS = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}


class RecebimentoXMLError(Exception):
    pass


@dataclass
class ItemRecebimentoXML:
    cod_prod: str
    descricao: str
    quantidade: Decimal


@dataclass
class DocumentoRecebimentoXML:
    chave_nfe: str
    numero: str
    tp_nf: str
    nat_op: str
    emit_nome: str
    emit_cnpj: str
    dest_nome: str
    dest_cnpj: str
    status_fiscal_cstat: str
    itens: list[ItemRecebimentoXML]
    tipo_documento: str = 'nfe'


def _texto(node, path):
    if node is None:
        return ''
    encontrado = node.find(path, SEFAZ_NS)
    return (encontrado.text or '').strip() if encontrado is not None else ''


def _digits(valor):
    return ''.join(ch for ch in (valor or '') if ch.isdigit())


def _parse_decimal(valor):
    if not valor:
        return Decimal('0')
    try:
        return Decimal(str(valor).replace(',', '.'))
    except (InvalidOperation, ValueError):
        return Decimal('0')


def parse_xml_recebimento(xml_file):
    xml_file.seek(0)
    try:
        root = ET.parse(xml_file).getroot()
    except ET.ParseError as exc:
        raise RecebimentoXMLError('XML inválido.') from exc

    inf_nfe = root.find('.//nfe:infNFe', SEFAZ_NS)
    if inf_nfe is None:
        raise RecebimentoXMLError('Estrutura XML da NFe não reconhecida para recebimento.')

    chave_nfe = (inf_nfe.attrib.get('Id') or '').replace('NFe', '').strip()
    if not chave_nfe:
        raise RecebimentoXMLError('Chave da NFe não encontrada no XML.')

    numero = _texto(inf_nfe, './/nfe:ide/nfe:nNF') or chave_nfe[-9:]
    tp_nf = _texto(inf_nfe, './/nfe:ide/nfe:tpNF')
    nat_op = _texto(inf_nfe, './/nfe:ide/nfe:natOp')
    emit_nome = _texto(inf_nfe, './/nfe:emit/nfe:xNome')
    emit_cnpj = _digits(_texto(inf_nfe, './/nfe:emit/nfe:CNPJ'))
    dest_nome = _texto(inf_nfe, './/nfe:dest/nfe:xNome')
    dest_cnpj = _digits(_texto(inf_nfe, './/nfe:dest/nfe:CNPJ'))
    cstat = _texto(root, './/nfe:protNFe/nfe:infProt/nfe:cStat')

    itens = []
    for det in inf_nfe.findall('.//nfe:det', SEFAZ_NS):
        cod_prod = _texto(det, './nfe:prod/nfe:cProd')
        descricao = _texto(det, './nfe:prod/nfe:xProd')
        quantidade = _parse_decimal(_texto(det, './nfe:prod/nfe:qCom'))
        if not cod_prod:
            continue
        itens.append(
            ItemRecebimentoXML(
                cod_prod=cod_prod,
                descricao=descricao or cod_prod,
                quantidade=quantidade,
            )
        )

    if not itens:
        raise RecebimentoXMLError('XML sem itens para recebimento.')

    xml_file.seek(0)
    return DocumentoRecebimentoXML(
        chave_nfe=chave_nfe,
        numero=str(numero),
        tp_nf=tp_nf,
        nat_op=nat_op,
        emit_nome=emit_nome,
        emit_cnpj=emit_cnpj,
        dest_nome=dest_nome,
        dest_cnpj=dest_cnpj,
        status_fiscal_cstat=cstat,
        itens=itens,
    )
