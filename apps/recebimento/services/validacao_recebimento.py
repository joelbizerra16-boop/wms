"""Regras de negócio para bloquear NF de venda e aceitar apenas entrada de recebimento."""

from __future__ import annotations

import re
from django.conf import settings

from apps.recebimento.services.xml_parser import DocumentoRecebimentoXML, RecebimentoXMLError

MENSAGEM_NF_VENDA = (
    'Esta NF parece ser uma nota de saída/venda. '
    'Não é permitido dar entrada em NF de venda.'
)

NAT_OP_VENDA_RE = re.compile(r'\bVENDA\b|\bVENDAS\b', re.IGNORECASE)
NAT_OP_BLOQUEIO = (
    'SAIDA',
    'SAÍDA',
    'REMESSA',
    'TRANSFERENCIA SAIDA',
    'TRANSFERÊNCIA SAIDA',
    'DEVOLUCAO DE COMPRA',
    'DEVOLUÇÃO DE COMPRA',
)

CSTAT_AUTORIZADA = {'100', '150'}


def _empresa_cnpj():
    return ''.join(ch for ch in (getattr(settings, 'WMS_EMPRESA_CNPJ', '') or '') if ch.isdigit())


def _emitente_eh_empresa(doc: DocumentoRecebimentoXML) -> bool:
    cnpj_empresa = _empresa_cnpj()
    emit_cnpj = doc.emit_cnpj or ''
    emit_nome = (doc.emit_nome or '').upper()
    if cnpj_empresa and emit_cnpj == cnpj_empresa:
        return True
    return 'BRIDA' in emit_nome


def _eh_nf_venda(doc: DocumentoRecebimentoXML) -> bool:
    if doc.tp_nf == '1':
        return True
    nat = (doc.nat_op or '').upper()
    nat_compacto = re.sub(r'\s+', ' ', nat)
    if NAT_OP_VENDA_RE.search(nat_compacto):
        return True
    for termo in NAT_OP_BLOQUEIO:
        if termo in nat_compacto:
            return True
    if not _emitente_eh_empresa(doc):
        return False
    dest_cnpj = doc.dest_cnpj or ''
    emit_cnpj = doc.emit_cnpj or ''
    if not dest_cnpj:
        return False
    if dest_cnpj == emit_cnpj:
        return False
    cnpj_empresa = _empresa_cnpj()
    if cnpj_empresa and dest_cnpj == cnpj_empresa:
        return False
    return True


def validar_documento_recebimento(doc: DocumentoRecebimentoXML):
    if doc.tipo_documento != 'nfe':
        raise RecebimentoXMLError('Documento não é uma NFe válida para recebimento.')

    if doc.status_fiscal_cstat and doc.status_fiscal_cstat not in CSTAT_AUTORIZADA:
        raise RecebimentoXMLError(
            f'NF com status fiscal não autorizado para entrada (cStat={doc.status_fiscal_cstat}).'
        )

    if _eh_nf_venda(doc):
        raise RecebimentoXMLError(MENSAGEM_NF_VENDA)

    if doc.tp_nf and doc.tp_nf != '0':
        raise RecebimentoXMLError(MENSAGEM_NF_VENDA)

    if not doc.tp_nf and not _emitente_eh_empresa(doc):
        pass
