"""Parsing de quantidades operacionais."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


class QuantidadeEstoqueError(Exception):
    pass


def parse_quantidade(valor) -> Decimal:
    texto = str(valor or '').strip().replace(',', '.')
    if not texto:
        raise QuantidadeEstoqueError('Informe a quantidade.')
    try:
        qtd = Decimal(texto)
    except (InvalidOperation, ValueError) as exc:
        raise QuantidadeEstoqueError('Quantidade inválida.') from exc
    if qtd <= 0:
        raise QuantidadeEstoqueError('Quantidade deve ser maior que zero.')
    return qtd
