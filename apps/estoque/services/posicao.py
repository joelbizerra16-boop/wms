"""Resolução e validação de posições para armazenagem e coletor."""

from __future__ import annotations

from decimal import Decimal

from apps.estoque.models import EstoqueFisico, PosicaoEstoque


class PosicaoEstoqueError(Exception):
    pass


MSG_EXCLUSAO_COM_SALDO = 'Não é possível excluir posição com saldo em estoque.'


def posicao_tem_saldo(posicao: PosicaoEstoque) -> bool:
    return EstoqueFisico.objects.filter(
        posicao=posicao,
        quantidade__gt=Decimal('0'),
    ).exists()


def inativar_posicao(posicao: PosicaoEstoque) -> None:
    if posicao_tem_saldo(posicao):
        raise PosicaoEstoqueError(MSG_EXCLUSAO_COM_SALDO)
    if posicao.status == PosicaoEstoque.Status.BLOQUEADA:
        raise PosicaoEstoqueError('Não é possível excluir posição bloqueada.')
    posicao.ativo = False
    posicao.save(update_fields=['ativo', 'updated_at'])


def montar_codigo_posicao(*, rua: str, posicao: str, andar: str, lado: str) -> str:
    partes = [p.strip() for p in (rua, posicao, andar, lado) if (p or '').strip()]
    return '-'.join(partes) if partes else ''


def resolver_posicao(entrada: str) -> PosicaoEstoque:
    """
    Resolve posição por código cadastrado ou leitura coletor (ex.: '1 1 2 1').
    """
    texto = (entrada or '').strip()
    if not texto:
        raise PosicaoEstoqueError('Informe o código ou endereço da posição.')

    codigo_especial = texto.strip().upper()
    if codigo_especial in ('TEMP', 'PULMAO', 'PULMÃO'):
        codigo_especial = 'PULMAO' if codigo_especial == 'PULMÃO' else codigo_especial
        pos, _ = PosicaoEstoque.objects.get_or_create(
            codigo_posicao=codigo_especial,
            defaults={
                'rua': codigo_especial,
                'posicao': '1',
                'andar': '1',
                'lado': '1',
                'setor': 'TEMP',
                'status': PosicaoEstoque.Status.ATIVA,
                'observacao': 'Posição logística (TEMP/PULMÃO) criada automaticamente.',
                'ativo': True,
            },
        )
        return _validar_posicao_operacional(pos)

    por_codigo = PosicaoEstoque.objects.filter(codigo_posicao__iexact=texto, ativo=True).first()
    if por_codigo:
        return _validar_posicao_operacional(por_codigo)

    tokens = texto.split()
    if len(tokens) == 4:
        rua, posicao, andar, lado = tokens
        encontrada = PosicaoEstoque.objects.filter(
            rua=rua,
            posicao=posicao,
            andar=andar,
            lado=lado,
            ativo=True,
        ).first()
        if encontrada:
            return _validar_posicao_operacional(encontrada)

    raise PosicaoEstoqueError(f'Posição não encontrada: {texto}')


def _validar_posicao_operacional(posicao: PosicaoEstoque) -> PosicaoEstoque:
    if not posicao.ativo:
        raise PosicaoEstoqueError(f'Posição {posicao.codigo_posicao} está inativa.')
    if posicao.status == PosicaoEstoque.Status.BLOQUEADA:
        raise PosicaoEstoqueError(f'Posição {posicao.codigo_posicao} está bloqueada.')
    if posicao.status == PosicaoEstoque.Status.MANUTENCAO:
        raise PosicaoEstoqueError(f'Posição {posicao.codigo_posicao} em manutenção.')
    return posicao
