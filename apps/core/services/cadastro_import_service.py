from __future__ import annotations

from pathlib import Path
import re
import unicodedata
from collections import Counter

import pandas as pd
from django.db.models import Q

from apps.clientes.models import Cliente
from apps.core.services.produto_sync_service import sincronizar_produtos_relacionados
from apps.produtos.models import GrupoAgregado, Produto
from apps.rotas.models import Rota
from apps.rotas.services.roteirizacao_service import normalizar_cep_para_int
from apps.tarefas.models import Tarefa

REQUIRED_PRODUCT_COLUMNS = {
    'COD_PROD': 'COD_PROD',
    'CODIGO': 'Código',
    'DESCRICAO': 'Descrição',
    'EMBALAGEM': 'EMBALAGEM',
    'CODIGO_DE_BARRAS_EAN': 'Código de Barras (EAN)',
    'SETOR': 'SETOR',
}
REQUIRED_ROTA_COLUMNS = {
    'PRACA': 'PRACA',
    'CEP_INICIAL': 'CEP_INICIAL',
    'CEP_FINAL': 'CEP_FINAL',
    'ROTA': 'ROTA',
}


def _clean_text(value):
    if value is None:
        return ''
    if pd.isna(value):
        return ''
    text = str(value).strip()
    if text.lower() in {'nan', 'none', '<na>'}:
        return ''
    return text


def _digits_only(value):
    return re.sub(r'\D', '', _clean_text(value))


def _pick(row, aliases):
    normalized = {str(col).strip().upper(): col for col in row.index}
    for alias in aliases:
        col = normalized.get(alias.upper())
        if col is not None:
            return row.get(col)
    return None


def _normalize_column_name(name):
    text = unicodedata.normalize('NFKD', str(name or ''))
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = text.strip().upper()
    text = re.sub(r'[^A-Z0-9]+', '_', text)
    return text.strip('_')


def _sanitize_code(value):
    text = _clean_text(value).upper()
    text = re.sub(r'[\|\s]+', '', text)
    text = re.sub(r'[^A-Z0-9\-_./]', '', text)
    return text[:50]


def _read_excel_or_csv(file_or_path):
    if isinstance(file_or_path, (str, Path)):
        name = str(file_or_path).lower()
    else:
        name = str(getattr(file_or_path, 'name', '')).lower()

    if name.endswith('.csv'):
        return pd.read_csv(file_or_path, dtype=str, keep_default_na=False)
    return pd.read_excel(file_or_path, dtype=str, keep_default_na=False)


def _is_valid_ean(ean):
    return bool(ean and ean != '0')


def _resolve_categoria_from_setor(setor):
    setor_upper = _clean_text(setor).upper()
    map_setor = {
        'FILTRO': Produto.Categoria.FILTROS,
        'FILTROS': Produto.Categoria.FILTROS,
        'LUBRIFICANTE': Produto.Categoria.LUBRIFICANTE,
        'AGREGADO': Produto.Categoria.AGREGADO,
        'NAO ENCONTRADO': Produto.Categoria.NAO_ENCONTRADO,
        'NAO_ENCONTRADO': Produto.Categoria.NAO_ENCONTRADO,
    }
    return map_setor.get(setor_upper, Produto.Categoria.NAO_ENCONTRADO)


def _vincular_grupo_agregado_por_setor(produto):
    setor = _clean_text(produto.setor).upper()
    if not setor:
        return
    grupo, _ = GrupoAgregado.objects.get_or_create(nome=setor)
    produto.grupos_agregados.add(grupo)


def importar_produtos_arquivo(file_or_path):
    if Tarefa.objects.filter(ativo=True, status__in=[Tarefa.Status.ABERTO, Tarefa.Status.EM_EXECUCAO]).exists():
        raise ValueError(
            'Existem tarefas abertas/em execução. Finalize ou pause as tarefas antes de importar uma nova base de produtos.'
        )

    df = _read_excel_or_csv(file_or_path).fillna('')
    total_linhas = int(len(df.index))
    original_columns = list(df.columns)
    df.columns = [_normalize_column_name(col) for col in df.columns]

    missing = [display for key, display in REQUIRED_PRODUCT_COLUMNS.items() if key not in set(df.columns)]
    if missing:
        raise ValueError(f'Estrutura inválida da planilha. Coluna(s) ausente(s): {", ".join(missing)}.')

    col_cod_prod = 'COD_PROD'
    col_codigo = 'CODIGO'
    col_descricao = 'DESCRICAO'
    col_embalagem = 'EMBALAGEM'
    col_ean = 'CODIGO_DE_BARRAS_EAN'
    col_setor = 'SETOR'

    rows = []
    ignored_reasons = Counter()
    for _, row in df.iterrows():
        cod_prod = _sanitize_code(row.get(col_cod_prod, ''))
        codigo = _sanitize_code(row.get(col_codigo, ''))
        ean = _digits_only(row.get(col_ean, ''))[:50]
        if ean == '0':
            ean = ''
        descricao = _clean_text(row.get(col_descricao, ''))
        embalagem = _clean_text(row.get(col_embalagem, ''))
        setor_raw = row.get(col_setor)
        setor = str(setor_raw).strip().upper() if _clean_text(setor_raw) else None

        if not any([cod_prod, codigo, ean, descricao, embalagem, setor]):
            ignored_reasons['linha_vazia'] += 1
            continue
        if not setor:
            ignored_reasons['setor_ausente'] += 1
            continue

        import_key = ''
        import_key_type = ''
        if _is_valid_ean(ean):
            import_key = ean
            import_key_type = 'ean'
        elif codigo:
            import_key = codigo
            import_key_type = 'codigo'
        elif cod_prod:
            import_key = cod_prod
            import_key_type = 'cod_prod'
        else:
            ignored_reasons['identificador_ausente'] += 1
            continue

        codigo_final = codigo or cod_prod or (_sanitize_code(ean) if ean else '')
        if not codigo_final:
            ignored_reasons['codigo_final_ausente'] += 1
            continue

        rows.append(
            {
                'import_key': import_key,
                'import_key_type': import_key_type,
                'codigo': codigo_final[:50],
                'cod_prod': cod_prod[:50],
                'codigo_col': codigo[:50],
                'ean': ean[:50],
                'descricao': descricao[:255],
                'embalagem': embalagem[:20] or None,
                'setor': setor[:50] if setor else None,
                'categoria': _resolve_categoria_from_setor(setor),
            }
        )

    ignorados = sum(ignored_reasons.values())
    if not rows:
        return {
            'total_processado': 0,
            'criados': 0,
            'atualizados': 0,
            'ignorados': ignorados,
            'ignorado_por_motivo': dict(ignored_reasons),
            'total_linhas': total_linhas,
        }

    codigos = {r['cod_prod'] for r in rows if r['cod_prod']}
    eans = {r['ean'] for r in rows if r['ean']}
    existentes = list(
        Produto.objects.filter(Q(cod_prod__in=codigos) | Q(cod_ean__in=eans))
    )
    por_codigo = {p.cod_prod: p for p in existentes}
    por_ean = {p.cod_ean: p for p in existentes if p.cod_ean}

    novos = []
    atualizacoes = []
    vistos_codigos = set()
    vistos_ids_update = set()

    for data in rows:
        codigo = data['codigo']
        cod_prod = data['cod_prod'] or codigo
        ean = data['ean']
        if data['import_key_type'] == 'ean':
            existente = por_ean.get(data['import_key']) or por_codigo.get(cod_prod)
        else:
            existente = por_codigo.get(cod_prod) or (por_ean.get(ean) if ean else None)

        if existente is None:
            if cod_prod in vistos_codigos:
                continue
            vistos_codigos.add(cod_prod)
            novos.append(
                Produto(
                    cod_prod=cod_prod,
                    codigo=codigo[:50] or None,
                    descricao=data['descricao'] or 'SEM DESCRICAO',
                    cod_ean=ean,
                    embalagem=data['embalagem'],
                    unidade=data['embalagem'],
                    setor=data['setor'],
                    categoria=data['categoria'],
                    ativo=True,
                    cadastrado_manual=True,
                    incompleto=False,
                )
            )
            continue

        alterou = False
        if data['descricao'] and existente.descricao != data['descricao']:
            existente.descricao = data['descricao']
            alterou = True
        if codigo and existente.codigo != codigo:
            existente.codigo = codigo
            alterou = True
        if data['embalagem'] and existente.embalagem != data['embalagem']:
            existente.embalagem = data['embalagem']
            alterou = True
        if data['embalagem'] and existente.unidade != data['embalagem']:
            existente.unidade = data['embalagem']
            alterou = True
        if ean and existente.cod_ean != ean:
            existente.cod_ean = ean
            alterou = True
        if data['setor'] and existente.setor != data['setor']:
            existente.setor = data['setor']
            alterou = True
        if data['categoria'] and existente.categoria != data['categoria']:
            existente.categoria = data['categoria']
            alterou = True
        if existente.incompleto:
            existente.incompleto = False
            alterou = True
        if not existente.cadastrado_manual:
            existente.cadastrado_manual = True
            alterou = True
        if not existente.ativo:
            existente.ativo = True
            alterou = True

        if alterou and existente.id not in vistos_ids_update:
            vistos_ids_update.add(existente.id)
            atualizacoes.append(existente)

    if novos:
        Produto.objects.bulk_create(novos, batch_size=1000)
    if atualizacoes:
        Produto.objects.bulk_update(
            atualizacoes,
            fields=['codigo', 'descricao', 'embalagem', 'cod_ean', 'setor', 'unidade', 'categoria', 'ativo', 'cadastrado_manual', 'incompleto', 'updated_at'],
            batch_size=1000,
        )
    if novos:
        for produto in Produto.objects.filter(cod_prod__in=[p.cod_prod for p in novos]):
            _vincular_grupo_agregado_por_setor(produto)
    if atualizacoes:
        for produto in atualizacoes:
            _vincular_grupo_agregado_por_setor(produto)
    sync_resultado = sincronizar_produtos_relacionados()

    return {
        'total_processado': len(rows),
        'criados': len(novos),
        'atualizados': len(atualizacoes),
        'ignorados': ignorados,
        'ignorado_por_motivo': dict(ignored_reasons),
        'total_linhas': total_linhas,
        'sincronizacao': sync_resultado,
    }


def importar_produtos_excel(path):
    return importar_produtos_arquivo(path)


def importar_rotas_arquivo(file_or_path):
    df = _read_excel_or_csv(file_or_path).fillna('')
    total_linhas = int(len(df.index))
    original_columns = list(df.columns)
    df.columns = [_normalize_column_name(col) for col in df.columns]

    missing = [display for key, display in REQUIRED_ROTA_COLUMNS.items() if key not in set(df.columns)]
    if missing:
        raise ValueError(f'Estrutura inválida da planilha de rotas. Coluna(s) ausente(s): {", ".join(missing)}.')

    rows = []
    ignorados = 0
    for _, row in df.iterrows():
        praca = _clean_text(row.get('PRACA'))
        cep_inicial_raw = _clean_text(row.get('CEP_INICIAL'))
        cep_final_raw = _clean_text(row.get('CEP_FINAL'))
        nome_rota = _clean_text(row.get('ROTA'))

        if not any([praca, cep_inicial_raw, cep_final_raw, nome_rota]):
            ignorados += 1
            continue
        if not nome_rota:
            ignorados += 1
            continue

        cep_inicial_num = normalizar_cep_para_int(cep_inicial_raw)
        cep_final_num = normalizar_cep_para_int(cep_final_raw)

        rows.append(
            {
                'praca': praca[:100] or None,
                'cep_inicial': cep_inicial_raw[:9] or None,
                'cep_final': cep_final_raw[:9] or None,
                'cep_inicial_num': cep_inicial_num,
                'cep_final_num': cep_final_num,
                'nome_rota': nome_rota[:100],
            }
        )

    if not rows:
        return {'total_linhas': total_linhas, 'total_processado': 0, 'criados': 0, 'atualizados': 0, 'ignorados': ignorados}

    nomes_rota = {r['nome_rota'] for r in rows}
    existentes = {r.nome_rota or r.nome: r for r in Rota.objects.filter(Q(nome_rota__in=nomes_rota) | Q(nome__in=nomes_rota))}

    novos = []
    atualizacoes = []
    for data in rows:
        existente = existentes.get(data['nome_rota'])
        if existente is None:
            novos.append(
                Rota(
                    nome=data['nome_rota'],
                    nome_rota=data['nome_rota'],
                    praca=data['praca'],
                    bairro=data['praca'],
                    cep_inicial=data['cep_inicial'],
                    cep_final=data['cep_final'],
                    cep_inicial_num=data['cep_inicial_num'],
                    cep_final_num=data['cep_final_num'],
                )
            )
            continue

        alterou = False
        if existente.nome != data['nome_rota']:
            existente.nome = data['nome_rota']
            alterou = True
        if existente.nome_rota != data['nome_rota']:
            existente.nome_rota = data['nome_rota']
            alterou = True
        if existente.praca != data['praca']:
            existente.praca = data['praca']
            alterou = True
        if existente.bairro != data['praca']:
            existente.bairro = data['praca']
            alterou = True
        if existente.cep_inicial != data['cep_inicial']:
            existente.cep_inicial = data['cep_inicial']
            alterou = True
        if existente.cep_final != data['cep_final']:
            existente.cep_final = data['cep_final']
            alterou = True
        if existente.cep_inicial_num != data['cep_inicial_num']:
            existente.cep_inicial_num = data['cep_inicial_num']
            alterou = True
        if existente.cep_final_num != data['cep_final_num']:
            existente.cep_final_num = data['cep_final_num']
            alterou = True
        if alterou:
            atualizacoes.append(existente)

    if novos:
        Rota.objects.bulk_create(novos, batch_size=1000)
    if atualizacoes:
        Rota.objects.bulk_update(
            atualizacoes,
            fields=['nome', 'nome_rota', 'praca', 'bairro', 'cep_inicial', 'cep_final', 'cep_inicial_num', 'cep_final_num', 'updated_at'],
            batch_size=1000,
        )

    return {
        'total_linhas': total_linhas,
        'total_processado': len(rows),
        'criados': len(novos),
        'atualizados': len(atualizacoes),
        'ignorados': ignorados,
    }


def _importar_clientes_df(df):
    criados = 0
    atualizados = 0
    ignorados = 0

    for _, row in df.iterrows():
        codigo = _clean_text(_pick(row, ['CODIGO', 'COD', 'CLIENTE_CODIGO', 'COD_CLIENTE']))
        nome = _clean_text(_pick(row, ['CLIENTE', 'NOME', 'RAZAO_SOCIAL', 'NOME_CLIENTE']))
        rota = _clean_text(_pick(row, ['ROTA', 'PRACA', 'PRAÇA', 'TRANSPORTADORA']))
        inscricao = _digits_only(_pick(row, ['IE', 'INSCRICAO_ESTADUAL', 'INSCRIÇÃO_ESTADUAL', 'CNPJ', 'CPF']))

        if not nome:
            ignorados += 1
            continue

        if not inscricao:
            # Mantem unicidade da modelagem atual sem bloquear importacao da planilha.
            base = codigo or nome
            inscricao = f'SEM-IE-{base[:35]}'

        obj, created = Cliente.objects.update_or_create(
            inscricao_estadual=inscricao[:50],
            defaults={
                'codigo': codigo[:50] or None,
                'nome': nome[:255],
                'rota': rota[:100] or None,
                'ativo': True,
            },
        )
        if created:
            criados += 1
        else:
            atualizados += 1

    return {'criados': criados, 'atualizados': atualizados, 'ignorados': ignorados}


def importar_clientes_arquivo(file_or_path):
    df = _read_excel_or_csv(file_or_path).fillna('')
    return _importar_clientes_df(df)


def importar_clientes_excel(path):
    df = pd.read_excel(path).fillna('')
    return _importar_clientes_df(df)


def importar_cadastros(planilha_produtos=None, planilha_clientes=None):
    base_dir = Path(__file__).resolve().parents[3]
    path_produtos = Path(planilha_produtos) if planilha_produtos else (base_dir / 'CAD_PROD.xlsx')
    path_clientes = Path(planilha_clientes) if planilha_clientes else (base_dir / 'PRACA.xls')

    if not path_produtos.exists():
        raise FileNotFoundError(f'Planilha de produtos nao encontrada: {path_produtos}')
    if not path_clientes.exists():
        raise FileNotFoundError(f'Planilha de clientes nao encontrada: {path_clientes}')

    resultado_produtos = importar_produtos_excel(path_produtos)
    resultado_clientes = importar_clientes_excel(path_clientes)

    return {
        'produtos': resultado_produtos,
        'clientes': resultado_clientes,
        'planilha_produtos': str(path_produtos),
        'planilha_clientes': str(path_clientes),
    }
