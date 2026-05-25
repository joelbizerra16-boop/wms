"""Importação XML exclusiva para estoque temporário — não aciona separação/conferência."""

from __future__ import annotations

import logging

from django.db import transaction

from apps.recebimento.models import EstoqueTemporario
from apps.recebimento.services.validacao_recebimento import validar_documento_recebimento
from apps.recebimento.services.xml_parser import RecebimentoXMLError, parse_xml_recebimento

logger = logging.getLogger(__name__)


def importar_xml_recebimento(xml_file, *, usuario, nome_arquivo=''):
    logger.info(
        'RECEBIMENTO_XML_START arquivo=%s user_id=%s',
        nome_arquivo or getattr(xml_file, 'name', ''),
        getattr(usuario, 'id', None),
    )
    documento = parse_xml_recebimento(xml_file)
    validar_documento_recebimento(documento)

    if EstoqueTemporario.objects.filter(
        chave_nfe=documento.chave_nfe,
        status=EstoqueTemporario.Status.TEMP,
    ).exists():
        raise RecebimentoXMLError(
            f'NF {documento.numero} já possui itens no estoque temporário (chave já importada).'
        )

    linhas = []
    with transaction.atomic():
        for item in documento.itens:
            linhas.append(
                EstoqueTemporario(
                    chave_nfe=documento.chave_nfe,
                    nf_numero=documento.numero,
                    produto_codigo=item.cod_prod,
                    descricao=item.descricao,
                    quantidade=item.quantidade,
                    usuario_recebimento=usuario,
                    canal=EstoqueTemporario.Canal.TEMP,
                    xml_origem=nome_arquivo or getattr(xml_file, 'name', ''),
                    status=EstoqueTemporario.Status.TEMP,
                    tp_nf=documento.tp_nf,
                    nat_op=documento.nat_op,
                    emitente_cnpj=documento.emit_cnpj,
                    destinatario_cnpj=documento.dest_cnpj,
                )
            )
        EstoqueTemporario.objects.bulk_create(linhas)

    logger.info(
        'RECEBIMENTO_XML_OK chave=%s nf=%s itens=%s user_id=%s',
        documento.chave_nfe,
        documento.numero,
        len(linhas),
        getattr(usuario, 'id', None),
    )
    return {
        'sucesso': True,
        'nf_numero': documento.numero,
        'chave_nfe': documento.chave_nfe,
        'quantidade_itens': len(linhas),
        'canal': EstoqueTemporario.Canal.TEMP,
    }
