"""URLs de retorno ao fluxo pocket após concluir separação ou conferência."""

from django.urls import reverse


def url_lista_separacao():
    return reverse('web-separacao-lista')


def url_exec_separacao(tarefa_id):
    return reverse('web-separacao-exec', kwargs={'tarefa_id': tarefa_id})


def url_lista_conferencia():
    return reverse('web-conferencia-lista')


def url_exec_conferencia(nf_id):
    return reverse('web-conferencia-exec', kwargs={'nf_id': nf_id})


def anexar_transicao_separacao(payload, usuario, *, tarefa_id_atual):
    """Sempre volta à lista; o operador escolhe e aceita a próxima tarefa manualmente."""
    del usuario, tarefa_id_atual
    payload['proxima_tarefa_id'] = None
    payload['redirect_url'] = url_lista_separacao()
    payload['tem_proxima'] = False
    return payload


def anexar_transicao_conferencia(payload, usuario, *, nf_id_atual):
    from apps.conferencia.services.conferencia_service import obter_proxima_nf_conferencia

    proxima_nf = obter_proxima_nf_conferencia(usuario, excluir_nf_id=nf_id_atual) if usuario is not None else None
    payload['proxima_nf_id'] = proxima_nf['id'] if proxima_nf else None
    payload['redirect_url'] = url_exec_conferencia(proxima_nf['id']) if proxima_nf else url_lista_conferencia()
    payload['tem_proxima'] = bool(proxima_nf)
    return payload
