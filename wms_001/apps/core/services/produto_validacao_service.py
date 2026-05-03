from dataclasses import dataclass

from django.db import OperationalError, connection
from django.utils import timezone

from apps.logs.models import Log
from apps.produtos.models import Produto


class ProdutoValidacaoError(Exception):
    pass


@dataclass
class ProdutoValidado:
    produto: Produto
    item: object
    codigo_lido_normalizado: str
    codigo_esperado: str
    setor_validado: str


def buscar_produto_por_leitura(codigo_lido):
    codigo_normalizado = _normalizar_codigo(codigo_lido)
    if not codigo_normalizado:
        return None
    return _buscar_produto_por_codigo(codigo_normalizado)


def validar_produto(codigo_lido, item_id, usuario, item_model, tipo_validacao):
    codigo_normalizado = _normalizar_codigo(codigo_lido)
    if not codigo_normalizado:
        raise ProdutoValidacaoError('Informe um código válido para bipagem.')

    item = (
        item_model.objects.select_for_update()
        .select_related('produto')
        .get(id=item_id)
    )
    produto_esperado = item.produto
    identificadores_esperados = _identificadores_produto(produto_esperado)
    if _codigo_corresponde_identificador(codigo_normalizado, identificadores_esperados):
        produto = produto_esperado
    else:
        produto = _buscar_produto_por_codigo(codigo_normalizado)
    if not produto:
        _log_validacao(
            usuario=usuario,
            tipo_validacao=tipo_validacao,
            codigo_lido=codigo_lido,
            produto_encontrado_id=None,
            item_id=item_id,
            produto_esperado_id=produto_esperado.id,
            produto_setor='',
            item_setor=(produto_esperado.setor or '').strip().upper(),
            resultado='produto_nao_cadastrado',
        )
        raise ProdutoValidacaoError('Produto não cadastrado.')

    codigo_esperado = _codigo_exibicao_produto(item.produto)
    produto_esperado_id = item.produto_id
    item_setor = (item.produto.setor or '').strip().upper()
    produto_setor = (produto.setor or '').strip().upper()

    if produto.id != produto_esperado_id:
        # Revalidação de proteção para reduzir falso negativo em concorrência.
        item_revalidado = (
            item_model.objects.select_related('produto')
            .get(id=item_id)
        )
        if produto.id == item_revalidado.produto_id:
            _log_validacao(
                usuario=usuario,
                tipo_validacao=tipo_validacao,
                codigo_lido=codigo_lido,
                produto_encontrado_id=produto.id,
                item_id=item_id,
                produto_esperado_id=item_revalidado.produto_id,
                produto_setor=produto_setor,
                item_setor=(item_revalidado.produto.setor or '').strip().upper(),
                resultado='aceito_pos_revalidacao',
            )
            return ProdutoValidado(
                produto=produto,
                item=item_revalidado,
                codigo_lido_normalizado=codigo_normalizado,
                codigo_esperado=_codigo_exibicao_produto(item_revalidado.produto),
                setor_validado=(item_revalidado.produto.setor or '').strip().upper(),
            )

        _log_validacao(
            usuario=usuario,
            tipo_validacao=tipo_validacao,
            codigo_lido=codigo_lido,
            produto_encontrado_id=produto.id,
            item_id=item_id,
            produto_esperado_id=item_revalidado.produto_id,
            produto_setor=produto_setor,
            item_setor=(item_revalidado.produto.setor or '').strip().upper(),
            resultado='produto_divergente',
        )
        raise ProdutoValidacaoError(
            f"Produto lido ({codigo_normalizado}) não corresponde ao item esperado "
            f"({_codigo_exibicao_produto(item_revalidado.produto)}) - verificar cadastro ou sincronização"
        )

    if produto_setor != item_setor:
        _log_validacao(
            usuario=usuario,
            tipo_validacao=tipo_validacao,
            codigo_lido=codigo_lido,
            produto_encontrado_id=produto.id,
            item_id=item_id,
            produto_esperado_id=produto_esperado_id,
            produto_setor=produto_setor,
            item_setor=item_setor,
            resultado='setor_divergente',
        )
        raise ProdutoValidacaoError(
            f'Produto do setor {produto_setor or "-"} não corresponde ao item do setor {item_setor or "-"}'
        )

    _log_validacao(
        usuario=usuario,
        tipo_validacao=tipo_validacao,
        codigo_lido=codigo_lido,
        produto_encontrado_id=produto.id,
        item_id=item_id,
        produto_esperado_id=produto_esperado_id,
        produto_setor=produto_setor,
        item_setor=item_setor,
        resultado='ok',
    )
    return ProdutoValidado(
        produto=produto,
        item=item,
        codigo_lido_normalizado=codigo_normalizado,
        codigo_esperado=codigo_esperado,
        setor_validado=item_setor,
    )


def _normalizar_codigo(valor):
    codigo = ''.join(str(valor or '').strip().split())
    if not codigo:
        return ''
    return codigo.upper()


def _codigo_exibicao_produto(produto):
    return str(produto.cod_prod or produto.codigo or '').strip()


def _codigo_variantes(codigo):
    codigo_base = _normalizar_codigo(codigo)
    if not codigo_base:
        return []
    variantes = [codigo_base]
    if codigo_base.isdigit():
        sem_zeros = codigo_base.lstrip('0') or '0'
        if sem_zeros not in variantes:
            variantes.append(sem_zeros)
    return variantes


def _identificadores_produto(produto):
    identificadores = set()
    for valor in [getattr(produto, 'cod_ean', ''), getattr(produto, 'cod_prod', ''), getattr(produto, 'codigo', '')]:
        identificadores.update(_codigo_variantes(valor))
    return identificadores


def _codigo_corresponde_identificador(codigo_lido_normalizado, identificadores):
    if not codigo_lido_normalizado or not identificadores:
        return False
    for variante in _codigo_variantes(codigo_lido_normalizado):
        if variante in identificadores:
            return True
    return False


def _buscar_produto_por_codigo(codigo_normalizado):
    if not codigo_normalizado:
        return None
    for variante in _codigo_variantes(codigo_normalizado):
        produto = Produto.objects.filter(cod_ean=variante, ativo=True).first()
        if produto:
            return produto
    for variante in _codigo_variantes(codigo_normalizado):
        produto = Produto.objects.filter(cod_prod=variante, ativo=True).first()
        if produto:
            return produto
    for variante in _codigo_variantes(codigo_normalizado):
        produto = Produto.objects.filter(codigo=variante, ativo=True).first()
        if produto:
            return produto
    return None


def _log_validacao(
    usuario,
    tipo_validacao,
    codigo_lido,
    produto_encontrado_id,
    item_id,
    produto_esperado_id,
    produto_setor,
    item_setor,
    resultado,
):
    if usuario is None:
        return
    try:
        Log.objects.create(
            usuario=usuario,
            acao=f'VALIDACAO PRODUTO {tipo_validacao}',
            detalhe=(
                f'codigo_lido={codigo_lido}; produto_encontrado_id={produto_encontrado_id}; '
                f'item_id={item_id}; produto_esperado_id={produto_esperado_id}; '
                f'produto_setor={produto_setor}; item_setor={item_setor}; '
                f'usuario={getattr(usuario, "id", None)}; '
                f'timestamp={timezone.now().strftime("%Y-%m-%d %H:%M:%S")}; resultado={resultado}'
            ),
        )
    except OperationalError as exc:
        if not (connection.vendor == 'sqlite' and 'database is locked' in str(exc).lower()):
            raise
