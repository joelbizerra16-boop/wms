"""URLs e metadados de transição entre tarefas/NFs no fluxo pocket."""

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
    from apps.tarefas.services.separacao_service import obter_proxima_tarefa_separacao

    proxima = obter_proxima_tarefa_separacao(usuario, excluir_tarefa_id=tarefa_id_atual)
    if proxima:
        payload['proxima_tarefa_id'] = proxima['id']
        payload['redirect_url'] = url_exec_separacao(proxima['id'])
        payload['tem_proxima'] = True
    else:
        payload['proxima_tarefa_id'] = None
        payload['redirect_url'] = url_lista_separacao()
        payload['tem_proxima'] = False
    return payload


def anexar_transicao_conferencia(payload, usuario, *, nf_id_atual):
    from apps.conferencia.services.conferencia_service import obter_proxima_nf_conferencia

    proxima = obter_proxima_nf_conferencia(usuario, excluir_nf_id=nf_id_atual)
    if proxima:
        payload['proxima_nf_id'] = proxima['id']
        payload['redirect_url'] = url_exec_conferencia(proxima['id'])
        payload['tem_proxima'] = True
    else:
        payload['proxima_nf_id'] = None
        payload['redirect_url'] = url_lista_conferencia()
        payload['tem_proxima'] = False
    return payload
