"""Regras de negócio para bloquear NF de saída da empresa e aceitar entrada de fornecedores."""

from __future__ import annotations

import logging
import re

from django.conf import settings

from apps.recebimento.services.xml_parser import DocumentoRecebimentoXML, RecebimentoXMLError

logger = logging.getLogger(__name__)

MENSAGEM_NF_VENDA = (
    'Esta NF parece ser uma nota de saída/venda. '
    'Não é permitido dar entrada em NF de venda.'
)

NAT_OP_BLOQUEIO_SAIDA = (
    'SAIDA',
    'SAÍDA',
    'REMESSA',
    'TRANSFERENCIA SAIDA',
    'TRANSFERÊNCIA SAIDA',
    'DEVOLUCAO DE COMPRA',
    'DEVOLUÇÃO DE COMPRA',
)

CSTAT_AUTORIZADA = {'100', '150'}


def _limpar_cnpj(valor: str) -> str:
    return ''.join(ch for ch in (valor or '') if ch.isdigit())


def _empresa_cnpj() -> str:
    return _limpar_cnpj(getattr(settings, 'WMS_EMPRESA_CNPJ', '') or '')


def _emitente_eh_empresa(doc: DocumentoRecebimentoXML) -> bool:
    cnpj_empresa = _empresa_cnpj()
    emit_cnpj = _limpar_cnpj(doc.emit_cnpj)
    emit_nome = (doc.emit_nome or '').upper()
    if cnpj_empresa and emit_cnpj == cnpj_empresa:
        return True
    return 'BRIDA' in emit_nome


def _destinatario_eh_empresa(doc: DocumentoRecebimentoXML) -> bool:
    cnpj_empresa = _empresa_cnpj()
    dest_cnpj = _limpar_cnpj(doc.dest_cnpj)
    dest_nome = (doc.dest_nome or '').upper()
    if cnpj_empresa and dest_cnpj == cnpj_empresa:
        return True
    return 'BRIDA' in dest_nome


def _nat_op_indica_saida_empresa(nat_op: str) -> bool:
    nat = re.sub(r'\s+', ' ', (nat_op or '').upper())
    for termo in NAT_OP_BLOQUEIO_SAIDA:
        if termo in nat:
            return True
    return False


def identificar_tipo_operacao(doc: DocumentoRecebimentoXML) -> dict:
    """
    Classifica a operação fiscal para recebimento TEMP.

    Entrada válida: destinatário é a empresa WMS (compra/recebimento de fornecedor).
    Saída proibida: emitente é a empresa WMS e destinatário é terceiro.
    """
    empresa = _empresa_cnpj()
    emitente = _limpar_cnpj(doc.emit_cnpj)
    destinatario = _limpar_cnpj(doc.dest_cnpj)
    tp_nf = doc.tp_nf or ''
    nat_op = doc.nat_op or ''

    info = {
        'emitente_cnpj': emitente,
        'destinatario_cnpj': destinatario,
        'empresa_cnpj': empresa,
        'tp_nf': tp_nf,
        'nat_op': nat_op,
        'entrada_valida': False,
        'decisao': 'BLOQUEADO',
        'motivo': '',
    }

    if empresa and destinatario == empresa:
        info['entrada_valida'] = True
        info['decisao'] = 'PERMITIDO'
        info['motivo'] = 'Fornecedor_para_empresa_WMS'
        return info

    if empresa and emitente == empresa and destinatario != empresa:
        info['motivo'] = 'Saida_empresa_WMS_para_terceiro'
        return info

    if not empresa:
        if _destinatario_eh_empresa(doc) and not _emitente_eh_empresa(doc):
            info['entrada_valida'] = True
            info['decisao'] = 'PERMITIDO'
            info['motivo'] = 'Fornecedor_para_BRIDA_sem_CNPJ_config'
            return info
        if _emitente_eh_empresa(doc) and not _destinatario_eh_empresa(doc):
            info['motivo'] = 'Saida_BRIDA_para_terceiro_sem_CNPJ_config'
            return info

    if _emitente_eh_empresa(doc) and _nat_op_indica_saida_empresa(nat_op):
        info['motivo'] = 'NatOp_saida_empresa'
        return info

    if _emitente_eh_empresa(doc) and destinatario and destinatario != emitente:
        info['motivo'] = 'Emitente_empresa_destino_terceiro'
        return info

    if _destinatario_eh_empresa(doc) and not _emitente_eh_empresa(doc):
        info['entrada_valida'] = True
        info['decisao'] = 'PERMITIDO'
        info['motivo'] = 'Fornecedor_para_BRIDA_heuristica'
        return info

    info['motivo'] = 'Operacao_nao_classificada_como_entrada'
    return info


def _log_validacao(doc: DocumentoRecebimentoXML, info: dict) -> None:
    logger.info(
        'RECEBIMENTO_XML_VALIDACAO emitente=%s destinatario=%s tp_nf=%s nat_op=%s '
        'decisao=%s motivo=%s entrada_valida=%s',
        info.get('emitente_cnpj') or '-',
        info.get('destinatario_cnpj') or '-',
        info.get('tp_nf') or '-',
        (info.get('nat_op') or '-')[:80],
        info.get('decisao'),
        info.get('motivo'),
        info.get('entrada_valida'),
    )


def validar_documento_recebimento(doc: DocumentoRecebimentoXML):
    if doc.tipo_documento != 'nfe':
        raise RecebimentoXMLError('Documento não é uma NFe válida para recebimento.')

    if doc.status_fiscal_cstat and doc.status_fiscal_cstat not in CSTAT_AUTORIZADA:
        raise RecebimentoXMLError(
            f'NF com status fiscal não autorizado para entrada (cStat={doc.status_fiscal_cstat}).'
        )

    info = identificar_tipo_operacao(doc)
    _log_validacao(doc, info)

    if not info['entrada_valida']:
        raise RecebimentoXMLError(MENSAGEM_NF_VENDA)
