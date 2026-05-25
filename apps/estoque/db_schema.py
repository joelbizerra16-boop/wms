"""Garantia de schema estoque no PostgreSQL (brownfield / deploy Render)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_TABELAS_ESTOQUE = (
    'estoque_posicaoestoque',
    'estoque_estoquefisico',
    'estoque_movimentacaoestoque',
)


def tabelas_estoque_existem(connection) -> bool:
    if connection.vendor != 'postgresql':
        return True
    try:
        existentes = set(connection.introspection.table_names())
    except Exception:
        return False
    return all(nome in existentes for nome in _TABELAS_ESTOQUE)


def aplicar_schema_estoque_brownfield(connection) -> bool:
    """
    Cria tabelas/índices do módulo estoque se ausentes (idempotente).
    Retorna True se schema ficou pronto.
    """
    if connection.vendor != 'postgresql':
        return True
    if tabelas_estoque_existem(connection):
        return True

    logger.warning('ESTOQUE_SCHEMA_APLICANDO tabelas ausentes — executando DDL brownfield')
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS estoque_posicaoestoque (
                id BIGSERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                codigo_posicao VARCHAR(80) NOT NULL UNIQUE,
                rua VARCHAR(30) NOT NULL,
                posicao VARCHAR(30) NOT NULL,
                andar VARCHAR(30) NOT NULL,
                lado VARCHAR(30) NOT NULL,
                setor VARCHAR(50) NOT NULL DEFAULT '',
                status VARCHAR(12) NOT NULL DEFAULT 'ATIVA',
                observacao VARCHAR(255) NOT NULL DEFAULT '',
                ativo BOOLEAN NOT NULL DEFAULT TRUE
            );
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS pos_est_status_ativo_ix
            ON estoque_posicaoestoque (status, ativo);
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS pos_est_endereco_ix
            ON estoque_posicaoestoque (rua, posicao, andar, lado);
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS estoque_estoquefisico (
                id BIGSERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                codigo_produto VARCHAR(50) NOT NULL,
                descricao VARCHAR(255) NOT NULL,
                quantidade NUMERIC(12, 2) NOT NULL,
                fifo_nf VARCHAR(32) NOT NULL,
                data_entrada TIMESTAMPTZ NOT NULL,
                nf_entrada VARCHAR(20) NOT NULL,
                chave_nfe VARCHAR(44) NOT NULL DEFAULT '',
                status VARCHAR(12) NOT NULL DEFAULT 'ATIVO',
                produto_id BIGINT NULL REFERENCES produtos_produto(id) DEFERRABLE INITIALLY DEFERRED,
                posicao_id BIGINT NOT NULL REFERENCES estoque_posicaoestoque(id) DEFERRABLE INITIALLY DEFERRED,
                estoque_temporario_id BIGINT NULL REFERENCES recebimento_estoquetemporario(id) DEFERRABLE INITIALLY DEFERRED,
                usuario_armazenagem_id BIGINT NOT NULL REFERENCES usuarios_usuario(id) DEFERRABLE INITIALLY DEFERRED
            );
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS est_fis_prod_data_ix
            ON estoque_estoquefisico (codigo_produto, data_entrada);
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS est_fis_fifo_ix
            ON estoque_estoquefisico (fifo_nf);
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS est_fis_pos_status_ix
            ON estoque_estoquefisico (posicao_id, status);
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS est_fis_status_qtd_ix
            ON estoque_estoquefisico (status, quantidade);
            """
        )

    ok = tabelas_estoque_existem(connection)
    if ok:
        logger.info('ESTOQUE_SCHEMA_OK')
    else:
        logger.error('ESTOQUE_SCHEMA_FALHA tabelas ainda ausentes apos DDL')
    return ok
