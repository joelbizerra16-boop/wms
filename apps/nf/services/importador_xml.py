from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
import xml.etree.ElementTree as ET

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.clientes.models import Cliente
from apps.conferencia.models import Conferencia, ConferenciaItem
from apps.logs.models import Log
from apps.nf.models import NotaFiscal, NotaFiscalItem
from apps.nf.services.status_service import sincronizar_status_operacional_nf
from apps.produtos.models import Produto
from apps.rotas.services.roteirizacao_service import definir_rota
from apps.tarefas.models import Tarefa, TarefaItem


class ImportacaoXMLError(Exception):
    pass


@dataclass
class ItemImportado:
    cod_prod: str
    descricao: str
    quantidade: Decimal
    cod_ean: str


@dataclass
class NFeProcessada:
    chave_nfe: str
    numero: str
    data_emissao: datetime | None
    cliente_nome: str | None
    inscricao_estadual: str | None
    cep: str | None
    bairro: str | None
    status_fiscal: str
    itens: list[ItemImportado]
    tipo_documento: str


SEFAZ_NS = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
STATUS_AUTORIZADA = {'100', '150'}
STATUS_CANCELADA = {'101', '135', '151', '155'}
STATUS_DENEGADA = {'110', '301', '302'}


STATUS_FISCAL_VALIDO = {
    NotaFiscal.StatusFiscal.AUTORIZADA,
    NotaFiscal.StatusFiscal.CANCELADA,
    NotaFiscal.StatusFiscal.DENEGADA,
}


def extrair_chave_nfe_xml(xml_file):
    xml_file.seek(0)
    try:
        tree = ET.parse(xml_file)
    except ET.ParseError as exc:
        raise ImportacaoXMLError('XML invalido.') from exc

    root = tree.getroot()
    chave_evento = _texto(root, './/nfe:infEvento/nfe:chNFe')
    if chave_evento:
        xml_file.seek(0)
        return chave_evento

    inf_nfe = root.find('.//nfe:infNFe', SEFAZ_NS)
    if inf_nfe is None:
        raise ImportacaoXMLError('Chave da NFe nao encontrada no XML.')

    chave = (inf_nfe.attrib.get('Id') or '').replace('NFe', '').strip()
    if not chave:
        raise ImportacaoXMLError('Chave da NFe nao encontrada no XML.')

    xml_file.seek(0)
    return chave


def extrair_resumo_nfe_xml(xml_file):
    xml_file.seek(0)
    try:
        tree = ET.parse(xml_file)
    except ET.ParseError as exc:
        raise ImportacaoXMLError('XML invalido.') from exc

    root = tree.getroot()
    inf_nfe = root.find('.//nfe:infNFe', SEFAZ_NS)
    if inf_nfe is None:
        chave_evento = _texto(root, './/nfe:infEvento/nfe:chNFe')
        if chave_evento:
            xml_file.seek(0)
            return {'chave_nfe': chave_evento, 'numero_nf': chave_evento[-9:]}
        raise ImportacaoXMLError('Chave da NFe nao encontrada no XML.')

    chave = (inf_nfe.attrib.get('Id') or '').replace('NFe', '').strip()
    if not chave:
        raise ImportacaoXMLError('Chave da NFe nao encontrada no XML.')
    numero_nf = _texto(inf_nfe, './/nfe:ide/nfe:nNF') or chave[-9:]
    xml_file.seek(0)
    return {'chave_nfe': chave, 'numero_nf': str(numero_nf)}


def importar_xml_nfe(xml_file, usuario=None, balcao=False, tarefas_lote_cache=None):
    xml_file.seek(0)
    try:
        tree = ET.parse(xml_file)
    except ET.ParseError as exc:
        raise ImportacaoXMLError('XML invalido.') from exc

    documento = _extrair_documento(tree.getroot())
    log_user = _obter_usuario_log(usuario)

    with transaction.atomic():
        nf_existente = (
            NotaFiscal.objects.select_related('rota', 'cliente')
            .prefetch_related('conferencias__itens', 'tarefas__itens')
            .filter(chave_nfe=documento.chave_nfe)
            .first()
        )

        if nf_existente:
            resultado_existente = _tratar_nf_existente(nf_existente, documento, log_user, balcao=balcao)
            _executar_validacao_final_automatica()
            return resultado_existente

        if documento.status_fiscal != NotaFiscal.StatusFiscal.AUTORIZADA:
            raise ImportacaoXMLError(
                f'NF {documento.numero} não importada: status inválido ({documento.status_fiscal}).'
            )

        if documento.tipo_documento == 'evento' and not documento.itens:
            raise ImportacaoXMLError('XML de evento sem NF correspondente nao pode ser importado.')

        cliente = _obter_ou_criar_cliente(documento)
        rota = definir_rota(documento.cep, documento.bairro)
        try:
            nf = NotaFiscal.objects.create(
                chave_nfe=documento.chave_nfe,
                numero=documento.numero,
                cliente=cliente,
                rota=rota,
                status=NotaFiscal.Status.PENDENTE,
                data_emissao=documento.data_emissao or timezone.now(),
                status_fiscal=documento.status_fiscal,
                bloqueada=documento.status_fiscal == NotaFiscal.StatusFiscal.CANCELADA,
                ativa=documento.status_fiscal == NotaFiscal.StatusFiscal.AUTORIZADA,
                balcao=bool(balcao),
            )
        except IntegrityError as exc:
            nf_existente = NotaFiscal.objects.filter(chave_nfe=documento.chave_nfe).first()
            if nf_existente:
                return _tratar_nf_existente(nf_existente, documento, log_user, balcao=balcao)
            raise

        produtos_novos = 0
        for item in documento.itens:
            produto, criado_automaticamente = _obter_ou_criar_produto_xml(item)
            if criado_automaticamente:
                produtos_novos += 1
            NotaFiscalItem.objects.create(
                nf=nf,
                produto=produto,
                quantidade=item.quantidade,
            )

        gerar_tarefas_separacao(nf, tarefas_lote_cache=tarefas_lote_cache)
        sincronizar_status_operacional_nf(nf)
        _validar_sanidade_nf(nf)
        _executar_validacao_final_automatica()

        Log.objects.create(
            usuario=log_user,
            acao='IMPORTACAO XML',
            detalhe=(
                f'NF {nf.numero} importada com {nf.itens.count()} item(ns). '
                f'Produtos criados automaticamente: {produtos_novos}.'
            ),
        )

        return {
            'sucesso': True,
            'erros': [],
            'quantidade_itens_importados': nf.itens.count(),
            'produtos_novos': produtos_novos,
            'status': 'sucesso',
            'mensagem': 'NF importada com sucesso',
            'nota_fiscal_id': nf.id,
            'chave_nfe': nf.chave_nfe,
        }


def _tratar_nf_existente(nf, documento, usuario_log, balcao=False):
    if documento.status_fiscal == NotaFiscal.StatusFiscal.CANCELADA:
        _bloquear_nf_cancelada(nf)
        Log.objects.create(
            usuario=usuario_log,
            acao='IMPORTACAO XML CANCELAMENTO',
            detalhe=f'NF {nf.numero} marcada como CANCELADA/BLOQUEADA por evento XML.',
        )
        return {
            'sucesso': True,
            'erros': [],
            'quantidade_itens_importados': 0,
            'produtos_novos': 0,
            'status': 'bloqueada',
            'mensagem': 'NF cancelada detectada e bloqueada na operação',
            'nota_fiscal_id': nf.id,
            'chave_nfe': nf.chave_nfe,
        }

    if documento.status_fiscal == NotaFiscal.StatusFiscal.DENEGADA:
        _bloquear_nf_denegada(nf)
        Log.objects.create(
            usuario=usuario_log,
            acao='IMPORTACAO XML DENEGADA',
            detalhe=f'NF {nf.numero} marcada como DENEGADA/BLOQUEADA por XML.',
        )
        return {
            'sucesso': True,
            'erros': [],
            'quantidade_itens_importados': 0,
            'produtos_novos': 0,
            'status': 'bloqueada',
            'mensagem': 'NF denegada detectada e bloqueada na operação',
            'nota_fiscal_id': nf.id,
            'chave_nfe': nf.chave_nfe,
        }

    if documento.status_fiscal == NotaFiscal.StatusFiscal.AUTORIZADA and bool(balcao) and not nf.balcao:
        # Permite promover para balcão em reimportação operacional.
        nf.balcao = True
        nf.save(update_fields=['balcao', 'updated_at'])
    Log.objects.create(
        usuario=usuario_log,
        acao='IMPORTACAO XML DUPLICADA',
        detalhe=f'NF {nf.numero} ja existia. Nenhum dado foi alterado pela importacao XML.',
    )
    return {
        'sucesso': True,
        'erros': [],
        'quantidade_itens_importados': 0,
        'produtos_novos': 0,
        'status': 'duplicada',
        'mensagem': 'NF já importada',
        'nota_fiscal_id': nf.id,
        'chave_nfe': nf.chave_nfe,
    }


def _bloquear_nf_cancelada(nf):
    nf.status_fiscal = NotaFiscal.StatusFiscal.CANCELADA
    nf.ativa = False
    nf.bloqueada = True
    nf.status = NotaFiscal.Status.BLOQUEADA_COM_RESTRICAO
    nf.save(update_fields=['status_fiscal', 'ativa', 'bloqueada', 'status', 'updated_at'])

    Tarefa.objects.filter(Q(nf=nf) | Q(itens__nf=nf), ativo=True).update(
        ativo=False,
        status=Tarefa.Status.FECHADO_COM_RESTRICAO,
        usuario=None,
        usuario_em_execucao=None,
        data_inicio=None,
    )
    conferencias_abertas = Conferencia.objects.filter(
        nf=nf,
        status__in=[Conferencia.Status.AGUARDANDO, Conferencia.Status.EM_CONFERENCIA, Conferencia.Status.LIBERADO_COM_RESTRICAO],
    )
    conferencia_ids = list(conferencias_abertas.values_list('id', flat=True))
    conferencias_abertas.update(status=Conferencia.Status.CANCELADA)
    if conferencia_ids:
        ConferenciaItem.objects.filter(conferencia_id__in=conferencia_ids).exclude(status=ConferenciaItem.Status.OK).update(
            status=ConferenciaItem.Status.CANCELADA
        )


def _bloquear_nf_denegada(nf):
    nf.status_fiscal = NotaFiscal.StatusFiscal.DENEGADA
    nf.ativa = False
    nf.bloqueada = True
    nf.status = NotaFiscal.Status.BLOQUEADA_COM_RESTRICAO
    nf.save(update_fields=['status_fiscal', 'ativa', 'bloqueada', 'status', 'updated_at'])
    Tarefa.objects.filter(Q(nf=nf) | Q(itens__nf=nf), ativo=True).update(
        ativo=False,
        status=Tarefa.Status.FECHADO_COM_RESTRICAO,
        usuario=None,
        usuario_em_execucao=None,
        data_inicio=None,
    )


def _obter_ou_criar_cliente(documento):
    inscricao = (documento.inscricao_estadual or '').strip()
    if not inscricao:
        inscricao = f'ISENTO-{documento.chave_nfe[-8:]}'

    cliente, created = Cliente.objects.get_or_create(
        inscricao_estadual=inscricao,
        defaults={'nome': (documento.cliente_nome or 'Cliente sem nome').strip()[:255]},
    )
    if not created and documento.cliente_nome and cliente.nome != documento.cliente_nome:
        cliente.nome = documento.cliente_nome[:255]
        cliente.save(update_fields=['nome', 'updated_at'])
    return cliente


def _obter_ou_criar_produto_xml(item):
    produto, created = Produto.objects.get_or_create(
        cod_prod=item.cod_prod,
        defaults={
            'descricao': item.descricao[:255],
            'cod_ean': item.cod_ean[:50],
            'unidade': None,
            'categoria': Produto.Categoria.NAO_ENCONTRADO,
            'ativo': True,
            'cadastrado_manual': False,
            'incompleto': True,
        },
    )
    campos_atualizados = []
    if not produto.descricao:
        produto.descricao = item.descricao[:255]
        campos_atualizados.append('descricao')
    elif produto.descricao != item.descricao[:255]:
        produto.descricao = item.descricao[:255]
        campos_atualizados.append('descricao')
    if item.cod_ean and (not produto.cod_ean or produto.cod_ean != item.cod_ean[:50]):
        produto.cod_ean = item.cod_ean[:50]
        campos_atualizados.append('cod_ean')
    categoria_normalizada = _normalizar_categoria_produto(produto, persistir=False)
    if produto.categoria != categoria_normalizada:
        produto.categoria = categoria_normalizada
        campos_atualizados.append('categoria')
    if campos_atualizados:
        campos_atualizados.append('updated_at')
        produto.save(update_fields=campos_atualizados)
    if created:
        return produto, True
    return produto, False


def gerar_tarefas_separacao(nf, tarefas_lote_cache=None):
    rota = nf.rota
    agrupados_por_setor = {}
    itens_filtros = []

    for item_nf in nf.itens.select_related('produto').all():
        produto = item_nf.produto
        categoria = _normalizar_categoria_produto(produto)
        if categoria == Produto.Categoria.FILTROS:
            itens_filtros.append((produto, item_nf.quantidade))
            continue
        agrupados_por_setor.setdefault(categoria, []).append((produto, item_nf.quantidade))

    if itens_filtros:
        tarefa_filtro, criada = Tarefa.objects.get_or_create(
            nf=nf,
            tipo=Tarefa.Tipo.FILTRO,
            setor=Produto.Categoria.FILTROS,
            rota=rota,
            defaults={'status': Tarefa.Status.ABERTO},
        )
        if criada:
            print(f'Tarefa criada para NF {nf.numero}')
        for produto, quantidade in itens_filtros:
            item_tarefa, item_criado = TarefaItem.objects.get_or_create(
                tarefa=tarefa_filtro,
                nf=nf,
                produto=produto,
                defaults={'quantidade_total': quantidade},
            )
            if not item_criado:
                item_tarefa.quantidade_total += quantidade
                item_tarefa.save(update_fields=['quantidade_total', 'updated_at'])

    for setor, itens in agrupados_por_setor.items():
        chave_lote = (setor, rota.id)
        tarefa = None
        if tarefas_lote_cache is not None:
            tarefa = tarefas_lote_cache.get(chave_lote)
        if tarefa is None:
            tarefa = Tarefa.objects.create(
                nf=None,
                tipo=Tarefa.Tipo.ROTA,
                setor=setor,
                rota=rota,
                status=Tarefa.Status.ABERTO,
            )
            if tarefas_lote_cache is not None:
                tarefas_lote_cache[chave_lote] = tarefa
            print(f'Tarefa criada para lote/NF {nf.numero}')
        for produto, quantidade in itens:
            item_tarefa = TarefaItem.objects.filter(tarefa=tarefa, produto=produto, nf=nf).first()
            if item_tarefa is None:
                TarefaItem.objects.create(
                    tarefa=tarefa,
                    nf=nf,
                    produto=produto,
                    quantidade_total=quantidade,
                )
                continue
            item_tarefa.quantidade_total += quantidade
            item_tarefa.save(update_fields=['quantidade_total', 'updated_at'])


def _normalizar_categoria_produto(produto, persistir=True):
    categorias_validas = {choice for choice, _label in Produto.Categoria.choices}
    categoria = (produto.categoria or '').strip()
    categoria_normalizada = categoria if categoria in categorias_validas else Produto.Categoria.NAO_ENCONTRADO
    if persistir and produto.categoria != categoria_normalizada:
        produto.categoria = categoria_normalizada
        produto.save(update_fields=['categoria', 'updated_at'])
    return categoria_normalizada


def _obter_usuario_log(usuario):
    if getattr(usuario, 'is_authenticated', False):
        return usuario

    user_model = get_user_model()
    sistema = user_model.objects.filter(username='sistema').first()
    if sistema:
        return sistema

    sistema = user_model.objects.create_user(
        username='sistema',
        nome='Sistema',
        perfil=user_model.Perfil.GESTOR,
        setor=user_model.Setor.NAO_ENCONTRADO,
        password=None,
        is_active=True,
        is_staff=False,
    )
    sistema.set_unusable_password()
    sistema.save(update_fields=['password'])
    return sistema


def _extrair_documento(root):
    inf_evento = root.find('.//nfe:infEvento', SEFAZ_NS)
    if inf_evento is not None:
        return _extrair_evento_cancelamento(root, inf_evento)

    inf_nfe = root.find('.//nfe:infNFe', SEFAZ_NS)
    if inf_nfe is None:
        raise ImportacaoXMLError('Estrutura XML da NFe nao reconhecida.')

    chave_nfe = (inf_nfe.attrib.get('Id') or '').replace('NFe', '').strip()
    if not chave_nfe:
        raise ImportacaoXMLError('Chave da NFe nao encontrada no XML.')

    numero = _texto(inf_nfe, './/nfe:ide/nfe:nNF')
    data_emissao = _parse_datetime(_texto(inf_nfe, './/nfe:ide/nfe:dhEmi') or _texto(inf_nfe, './/nfe:ide/nfe:dEmi'))
    cliente_nome = _texto(inf_nfe, './/nfe:dest/nfe:xNome')
    inscricao_estadual = _texto(inf_nfe, './/nfe:dest/nfe:IE')
    cep = _normalizar_cep(_texto(inf_nfe, './/nfe:dest/nfe:enderDest/nfe:CEP'))
    bairro = _texto(inf_nfe, './/nfe:dest/nfe:enderDest/nfe:xBairro')
    status_fiscal = _determinar_status_fiscal(root)

    itens = []
    for det in inf_nfe.findall('.//nfe:det', SEFAZ_NS):
        cod_prod = _texto(det, './nfe:prod/nfe:cProd')
        descricao = _texto(det, './nfe:prod/nfe:xProd')
        quantidade = _parse_decimal(_texto(det, './nfe:prod/nfe:qCom'))
        cod_ean = _texto(det, './nfe:prod/nfe:cEAN') or ''

        if not cod_prod or not descricao:
            raise ImportacaoXMLError('Item da NF sem codigo ou descricao do produto.')

        itens.append(
            ItemImportado(
                cod_prod=cod_prod,
                descricao=descricao,
                quantidade=quantidade,
                cod_ean=cod_ean,
            )
        )

    if not itens and status_fiscal == NotaFiscal.StatusFiscal.AUTORIZADA:
        raise ImportacaoXMLError('XML da NFe sem itens para importacao.')

    return NFeProcessada(
        chave_nfe=chave_nfe,
        numero=numero or chave_nfe[-9:],
        data_emissao=data_emissao,
        cliente_nome=cliente_nome,
        inscricao_estadual=inscricao_estadual,
        cep=cep,
        bairro=bairro,
        status_fiscal=status_fiscal,
        itens=itens,
        tipo_documento='nfe',
    )


def _extrair_evento_cancelamento(root, inf_evento):
    chave_nfe = _texto(inf_evento, './nfe:chNFe')
    if not chave_nfe:
        raise ImportacaoXMLError('Evento XML sem chave da NFe.')

    return NFeProcessada(
        chave_nfe=chave_nfe,
        numero=chave_nfe[-9:],
        data_emissao=None,
        cliente_nome=None,
        inscricao_estadual=None,
        cep=None,
        bairro=None,
        status_fiscal=NotaFiscal.StatusFiscal.CANCELADA,
        itens=[],
        tipo_documento='evento',
    )


def _determinar_status_fiscal(root):
    cstat = _texto(root, './/nfe:protNFe/nfe:infProt/nfe:cStat') or _texto(root, './/nfe:retEvento/nfe:infEvento/nfe:cStat')
    if not cstat:
        raise ImportacaoXMLError('NF sem status fiscal (cStat) no XML.')
    if cstat in STATUS_CANCELADA:
        return NotaFiscal.StatusFiscal.CANCELADA
    if cstat in STATUS_DENEGADA:
        return NotaFiscal.StatusFiscal.DENEGADA
    if cstat in STATUS_AUTORIZADA:
        return NotaFiscal.StatusFiscal.AUTORIZADA
    raise ImportacaoXMLError(f'NF com status fiscal inválido para importação (cStat={cstat}).')


def analisar_xml_nfe(xml_file):
    xml_file.seek(0)
    try:
        tree = ET.parse(xml_file)
    except ET.ParseError as exc:
        raise ImportacaoXMLError('XML invalido.') from exc
    documento = _extrair_documento(tree.getroot())
    xml_file.seek(0)
    return documento


def _validar_sanidade_nf(nf):
    if nf.status_fiscal not in STATUS_FISCAL_VALIDO:
        raise ImportacaoXMLError(f'NF {nf.numero} com status fiscal inválido no banco.')
    if nf.status_fiscal == NotaFiscal.StatusFiscal.CANCELADA and nf.ativa:
        raise ImportacaoXMLError(f'NF {nf.numero} cancelada não pode estar ativa.')
    if NotaFiscal.objects.filter(chave_nfe=nf.chave_nfe).count() > 1:
        raise ImportacaoXMLError(f'Duplicidade detectada para chave {nf.chave_nfe}.')


def _executar_validacao_final_automatica():
    duplicadas = (
        NotaFiscal.objects.values('chave_nfe')
        .annotate(total=Count('id'))
        .filter(total__gt=1)
        .count()
    )
    canceladas_ativas = NotaFiscal.objects.filter(
        status_fiscal=NotaFiscal.StatusFiscal.CANCELADA,
        ativa=True,
    ).count()
    status_invalidos = NotaFiscal.objects.exclude(status_fiscal__in=STATUS_FISCAL_VALIDO).count()

    inconsistencias = []
    if duplicadas:
        inconsistencias.append(f'{duplicadas} chave(s) duplicada(s)')
    if canceladas_ativas:
        inconsistencias.append(f'{canceladas_ativas} NF(s) cancelada(s) ativa(s)')
    if status_invalidos:
        inconsistencias.append(f'{status_invalidos} NF(s) com status fiscal inválido')

    if inconsistencias:
        raise ImportacaoXMLError(
            'Validação final automática falhou: ' + '; '.join(inconsistencias) + '.'
        )


def _texto(node, path):
    if node is None:
        return None
    encontrado = node.find(path, SEFAZ_NS)
    if encontrado is None or encontrado.text is None:
        return None
    return encontrado.text.strip()


def _parse_decimal(valor):
    if valor in (None, ''):
        raise ImportacaoXMLError('Quantidade do item nao informada no XML.')
    try:
        return Decimal(str(valor).replace(',', '.'))
    except (InvalidOperation, TypeError) as exc:
        raise ImportacaoXMLError('Quantidade do item invalida no XML.') from exc


def _parse_datetime(valor):
    if not valor:
        return None
    try:
        if valor.endswith('Z'):
            valor = valor[:-1] + '+00:00'
        parsed = datetime.fromisoformat(valor)
    except ValueError:
        try:
            parsed = datetime.strptime(valor, '%Y-%m-%d')
        except ValueError as exc:
            raise ImportacaoXMLError('Data de emissao invalida no XML.') from exc
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _normalizar_cep(valor):
    if not valor:
        return None
    digits = re.sub(r'\D', '', valor)
    return digits or None