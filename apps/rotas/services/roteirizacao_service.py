import re

from apps.rotas.models import Rota


ROTA_FALLBACK_AJUSTAR = 'AJUSTAR'


def normalizar_cep_para_int(cep):
    if cep is None:
        return None
    digits = re.sub(r'\D', '', str(cep))
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def definir_rota(cep, praca=None):
    cep_int = normalizar_cep_para_int(cep)
    praca_norm = (praca or '').strip()

    if cep_int is not None:
        rota_por_cep = (
            Rota.objects.filter(
                cep_inicial_num__isnull=False,
                cep_final_num__isnull=False,
                cep_inicial_num__lte=cep_int,
                cep_final_num__gte=cep_int,
            )
            .order_by('cep_inicial_num')
            .first()
        )
        if rota_por_cep is not None:
            return rota_por_cep

    if praca_norm:
        rota_por_praca = Rota.objects.filter(praca__iexact=praca_norm).first()
        if rota_por_praca is not None:
            return rota_por_praca
        rota_por_bairro = Rota.objects.filter(bairro__iexact=praca_norm).first()
        if rota_por_bairro is not None:
            return rota_por_bairro

    rota_ajustar, _ = Rota.objects.get_or_create(
        nome=ROTA_FALLBACK_AJUSTAR,
        defaults={
            'nome_rota': ROTA_FALLBACK_AJUSTAR,
            'praca': 'AJUSTAR',
            'bairro': 'AJUSTAR',
        },
    )
    return rota_ajustar
