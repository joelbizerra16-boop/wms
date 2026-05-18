from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.conferencia.models import Conferencia
from apps.nf.models import NotaFiscal, NotaFiscalItem
from apps.tarefas.models import Tarefa


STRUCTURAL_MODELS = (NotaFiscal, NotaFiscalItem, Tarefa, Conferencia)


def _invalidar_fila_conferencia(motivo):
    from apps.conferencia.services.conferencia_service import invalidate_nfs_disponiveis_cache
    from apps.core.services.visibilidade_operacional_service import invalidate_monitoramento_conferencia_cache

    def _on_commit():
        invalidate_nfs_disponiveis_cache(motivo=motivo)
        invalidate_monitoramento_conferencia_cache(motivo=motivo)

    transaction.on_commit(_on_commit)


@receiver(post_save, sender=NotaFiscal)
@receiver(post_save, sender=NotaFiscalItem)
@receiver(post_save, sender=Tarefa)
@receiver(post_save, sender=Conferencia)
def invalidar_conferencia_apos_save(sender, **kwargs):
    _invalidar_fila_conferencia(f'{sender.__name__.lower()}_save')


@receiver(post_delete, sender=NotaFiscal)
@receiver(post_delete, sender=NotaFiscalItem)
@receiver(post_delete, sender=Tarefa)
@receiver(post_delete, sender=Conferencia)
def invalidar_conferencia_apos_delete(sender, **kwargs):
    _invalidar_fila_conferencia(f'{sender.__name__.lower()}_delete')
