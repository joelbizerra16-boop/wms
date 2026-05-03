from django.db import transaction

from apps.conferencia.models import ConferenciaItem
from apps.produtos.models import Produto
from apps.tarefas.models import TarefaItem


def _clean_text(value):
    return str(value or '').strip().upper()


def _digits_only(value):
    return ''.join(ch for ch in str(value or '') if ch.isdigit())


def _normalizar_codigos_produto(produto):
    alterou = False
    codigo = _clean_text(produto.codigo)
    cod_prod = _clean_text(produto.cod_prod)
    cod_ean = _digits_only(produto.cod_ean)
    descricao = str(produto.descricao or '').strip().upper()
    setor = _clean_text(produto.setor)

    if produto.codigo != (codigo or None):
        produto.codigo = codigo or None
        alterou = True
    if produto.cod_prod != cod_prod:
        produto.cod_prod = cod_prod
        alterou = True
    if produto.cod_ean != (cod_ean or None):
        produto.cod_ean = cod_ean or None
        alterou = True
    if produto.descricao != descricao:
        produto.descricao = descricao
        alterou = True
    if produto.setor != (setor or None):
        produto.setor = setor or None
        alterou = True
    if alterou:
        produto.save(
            update_fields=['codigo', 'cod_prod', 'cod_ean', 'descricao', 'setor', 'updated_at']
        )
    return alterou


def _candidatos_por_identificadores(produto_origem):
    ean = _digits_only(produto_origem.cod_ean)
    cod_prod = _clean_text(produto_origem.cod_prod)
    codigo = _clean_text(produto_origem.codigo)

    if ean:
        candidato = Produto.objects.filter(cod_ean=ean, ativo=True).order_by('-updated_at', '-id').first()
        if candidato:
            return candidato
    if cod_prod:
        candidato = Produto.objects.filter(cod_prod=cod_prod, ativo=True).order_by('-updated_at', '-id').first()
        if candidato:
            return candidato
    if codigo:
        candidato = Produto.objects.filter(codigo=codigo, ativo=True).order_by('-updated_at', '-id').first()
        if candidato:
            return candidato
    return None


def detectar_eans_duplicados():
    duplicados = {}
    for produto in Produto.objects.exclude(cod_ean__isnull=True).exclude(cod_ean='').values('cod_ean'):
        ean = _digits_only(produto.get('cod_ean'))
        if not ean:
            continue
        duplicados[ean] = duplicados.get(ean, 0) + 1
    return {ean: qtd for ean, qtd in duplicados.items() if qtd > 1}


def sincronizar_referencias_produto(produto):
    itens_tarefa_corrigidos = 0
    itens_conferencia_corrigidos = 0
    if produto is None:
        return {
            'itens_tarefa_corrigidos': 0,
            'itens_conferencia_corrigidos': 0,
        }

    with transaction.atomic():
        for item in TarefaItem.objects.select_related('produto').filter(produto=produto).iterator(chunk_size=500):
            candidato = _candidatos_por_identificadores(item.produto)
            if candidato and candidato.id != item.produto_id:
                item.produto = candidato
                item.save(update_fields=['produto', 'updated_at'])
                itens_tarefa_corrigidos += 1

        for item in ConferenciaItem.objects.select_related('produto').filter(produto=produto).iterator(chunk_size=500):
            candidato = _candidatos_por_identificadores(item.produto)
            if candidato and candidato.id != item.produto_id:
                item.produto = candidato
                item.save(update_fields=['produto', 'updated_at'])
                itens_conferencia_corrigidos += 1

    return {
        'itens_tarefa_corrigidos': itens_tarefa_corrigidos,
        'itens_conferencia_corrigidos': itens_conferencia_corrigidos,
    }


def sincronizar_produtos_relacionados():
    produtos_normalizados = 0
    itens_tarefa_corrigidos = 0
    itens_conferencia_corrigidos = 0
    itens_nao_encontrados = 0

    with transaction.atomic():
        for produto in Produto.objects.all().iterator(chunk_size=500):
            if _normalizar_codigos_produto(produto):
                produtos_normalizados += 1

        for item in TarefaItem.objects.select_related('produto').all().iterator(chunk_size=500):
            candidato = _candidatos_por_identificadores(item.produto)
            if candidato and candidato.id != item.produto_id:
                item.produto = candidato
                item.save(update_fields=['produto', 'updated_at'])
                itens_tarefa_corrigidos += 1
                continue
            if candidato is None:
                itens_nao_encontrados += 1
                if item.produto.categoria != Produto.Categoria.NAO_ENCONTRADO or item.produto.setor != 'NAO_ENCONTRADO':
                    item.produto.categoria = Produto.Categoria.NAO_ENCONTRADO
                    item.produto.setor = 'NAO_ENCONTRADO'
                    item.produto.save(update_fields=['categoria', 'setor', 'updated_at'])

        for item in ConferenciaItem.objects.select_related('produto').all().iterator(chunk_size=500):
            candidato = _candidatos_por_identificadores(item.produto)
            if candidato and candidato.id != item.produto_id:
                item.produto = candidato
                item.save(update_fields=['produto', 'updated_at'])
                itens_conferencia_corrigidos += 1
                continue
            if candidato is None:
                itens_nao_encontrados += 1
                if item.produto.categoria != Produto.Categoria.NAO_ENCONTRADO or item.produto.setor != 'NAO_ENCONTRADO':
                    item.produto.categoria = Produto.Categoria.NAO_ENCONTRADO
                    item.produto.setor = 'NAO_ENCONTRADO'
                    item.produto.save(update_fields=['categoria', 'setor', 'updated_at'])

    return {
        'produtos_normalizados': produtos_normalizados,
        'itens_tarefa_corrigidos': itens_tarefa_corrigidos,
        'itens_conferencia_corrigidos': itens_conferencia_corrigidos,
        'itens_nao_encontrados': itens_nao_encontrados,
        'eans_duplicados': detectar_eans_duplicados(),
    }
