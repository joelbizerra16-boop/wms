import logging
import gzip
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile

from apps.logs.models import Log

logger = logging.getLogger(__name__)


class XMLStorageUnavailableError(FileNotFoundError):
    pass


def _read_file_bytes(xml_file):
    try:
        xml_file.seek(0)
    except Exception:
        pass
    content = xml_file.read()
    if isinstance(content, str):
        content = content.encode('utf-8')
    if not isinstance(content, (bytes, bytearray)):
        content = bytes(content or b'')
    try:
        xml_file.seek(0)
    except Exception:
        pass
    return bytes(content)


def has_entrada_xml_backup(entrada):
    return bool(getattr(entrada, 'xml_backup_gzip', None))


def store_entrada_xml_backup(entrada, xml_file, save=True):
    content = _read_file_bytes(xml_file)
    if not content:
        return False

    compressed = gzip.compress(content)
    entrada.xml_backup_gzip = compressed
    if save:
        entrada.save(update_fields=['xml_backup_gzip', 'updated_at'])
    return True


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


def _recover_xml_from_backup(entrada, user=None):
    compressed = getattr(entrada, 'xml_backup_gzip', None)
    if not compressed:
        return False

    try:
        content = gzip.decompress(bytes(compressed))
    except Exception as exc:
        _safe_register_inconsistency(
            user,
            f'Backup XML invalido para entrada {entrada.id}, chave {entrada.chave_nf}: {exc}',
        )
        return False

    xml_name = (getattr(entrada.xml, 'name', '') or '').strip()
    if not xml_name:
        xml_name = f'xmls/{entrada.chave_nf}.xml'

    entrada.xml.save(xml_name, ContentFile(content), save=False)
    entrada.save(update_fields=['xml', 'updated_at'])
    _safe_register_inconsistency(
        user,
        f'XML da entrada {entrada.id} restaurado do backup persistente no banco.',
    )
    return True


def _recover_xml_to_storage(entrada, user=None):
    xml_name = (getattr(entrada.xml, 'name', '') or '').strip()
    if not xml_name:
        return False

    for candidate in _candidate_paths(xml_name):
        if not candidate.exists() or not candidate.is_file():
            continue
        content = candidate.read_bytes()
        entrada.xml.save(xml_name, ContentFile(content), save=False)
        entrada.xml_backup_gzip = gzip.compress(content)
        entrada.save(update_fields=['xml', 'xml_backup_gzip', 'updated_at'])
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
            if not has_entrada_xml_backup(entrada):
                try:
                    with entrada.xml.open('rb') as stream:
                        store_entrada_xml_backup(entrada, stream)
                except Exception:
                    logger.exception('Falha ao registrar backup persistente do XML da entrada %s.', entrada.id)
            return True
    except Exception as exc:
        detail = f'Falha ao consultar storage do XML da entrada {entrada.id} ({xml_name}): {exc}'
        _safe_register_inconsistency(user, detail)
        return False

    if _recover_xml_from_backup(entrada, user=user):
        return True

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