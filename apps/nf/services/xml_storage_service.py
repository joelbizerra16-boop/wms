import logging
from pathlib import Path

from django.conf import settings
from django.core.files import File

from apps.logs.models import Log

logger = logging.getLogger(__name__)


class XMLStorageUnavailableError(FileNotFoundError):
    pass


def _safe_register_inconsistency(user, detail):
    logger.error(detail)
    if user is None or not getattr(user, 'is_authenticated', False):
        return
    try:
        Log.objects.create(usuario=user, acao='XML STORAGE INCONSISTENTE', detalhe=detail)
    except Exception:
        logger.exception('Falha ao registrar auditoria de inconsistencia de XML.')


def _candidate_paths(xml_name):
    base_dir = Path(settings.BASE_DIR)
    basename = Path(xml_name).name
    return [
        Path(settings.MEDIA_ROOT) / xml_name,
        base_dir / xml_name,
        base_dir / 'media' / xml_name,
        base_dir / 'xmls' / basename,
    ]


def _recover_xml_to_storage(entrada, user=None):
    xml_name = (getattr(entrada.xml, 'name', '') or '').strip()
    if not xml_name:
        return False

    for candidate in _candidate_paths(xml_name):
        if not candidate.exists() or not candidate.is_file():
            continue
        with candidate.open('rb') as stream:
            entrada.xml.save(xml_name, File(stream), save=False)
        entrada.save(update_fields=['xml', 'updated_at'])
        _safe_register_inconsistency(
            user,
            f'XML da entrada {entrada.id} recuperado de arquivo legado local: {candidate}',
        )
        return True
    return False


def ensure_entrada_xml_available(entrada, user=None):
    xml_name = (getattr(entrada.xml, 'name', '') or '').strip()
    if not xml_name:
        detail = f'Entrada {entrada.id} sem XML associado para chave {entrada.chave_nf}.'
        _safe_register_inconsistency(user, detail)
        return False

    try:
        if entrada.xml.storage.exists(xml_name):
            return True
    except Exception as exc:
        detail = f'Falha ao consultar storage do XML da entrada {entrada.id} ({xml_name}): {exc}'
        _safe_register_inconsistency(user, detail)
        return False

    if _recover_xml_to_storage(entrada, user=user):
        return True

    detail = (
        f'XML ausente para entrada {entrada.id}, chave {entrada.chave_nf}, arquivo {xml_name}. '
        'Banco aponta para arquivo inexistente no storage atual.'
    )
    _safe_register_inconsistency(user, detail)
    return False


def open_entrada_xml(entrada, user=None, mode='rb'):
    if not ensure_entrada_xml_available(entrada, user=user):
        raise XMLStorageUnavailableError(
            f'Arquivo XML não encontrado para a entrada {entrada.id} ({getattr(entrada.xml, "name", "sem_nome")}).'
        )
    return entrada.xml.open(mode)