"""FIFO por data de entrada + NF (MM/AA-NUMERO_NF)."""

from __future__ import annotations

from datetime import datetime


def formatar_fifo_nf(data_entrada: datetime, nf_numero: str) -> str:
    nf = (nf_numero or '').strip()
    if not data_entrada:
        return nf or '-'
    mm_aa = data_entrada.strftime('%m/%y')
    return f'{mm_aa}-{nf}' if nf else mm_aa
