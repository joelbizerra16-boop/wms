"""Helpers de bipagem para testes (janela anti-duplicata do scanner enterprise)."""

import time

from apps.core.bipagem_leitura import JANELA_ANTI_DUPLICATA_MS

_INTERVALO_BIPAGEM_TESTE = (JANELA_ANTI_DUPLICATA_MS / 1000.0) + 0.15


def pausa_anti_duplicata_bipagem():
    time.sleep(_INTERVALO_BIPAGEM_TESTE)


def bipar_codigo(client, url, payload, codigo, quantidade, *, assert_status=200):
    for _ in range(int(quantidade)):
        response = client.post(url, {**payload, 'codigo': codigo}, format='json')
        if assert_status is not None:
            assert response.status_code == assert_status, getattr(response, 'data', response.content)
        pausa_anti_duplicata_bipagem()
        yield response
