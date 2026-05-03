def _read(source, key):
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)


def extract_nf_from_chave_nfe(chave_nfe):
    chave = ''.join(ch for ch in str(chave_nfe or '') if ch.isdigit())
    if len(chave) < 34:
        return None
    return chave[25:34]


def resolve_nf_numero(*, obj=None, chave_nfe=None, logger=None, context=''):
    nf_direta = _read(obj, 'nf_numero')
    if nf_direta:
        return str(nf_direta)

    tarefa = _read(obj, 'tarefa')
    if tarefa is not None:
        tarefa_nf_numero = _read(tarefa, 'nf_numero')
        if tarefa_nf_numero:
            return str(tarefa_nf_numero)
        tarefa_nf = _read(tarefa, 'nf')
        if tarefa_nf is not None and _read(tarefa_nf, 'numero'):
            return str(_read(tarefa_nf, 'numero'))

    item = _read(obj, 'item')
    if item is not None:
        item_nf_numero = _read(item, 'nf_numero')
        if item_nf_numero:
            return str(item_nf_numero)
        item_nf = _read(item, 'nf')
        if item_nf is not None and _read(item_nf, 'numero'):
            return str(_read(item_nf, 'numero'))

    chave = chave_nfe if chave_nfe is not None else _read(obj, 'chave_nfe')
    nf_via_chave = extract_nf_from_chave_nfe(chave)
    if nf_via_chave:
        return nf_via_chave

    if logger:
        logger.warning('NF nao encontrada para rastreabilidade. context=%s obj_type=%s', context or '-', type(obj).__name__ if obj is not None else '-')
    return '-'
