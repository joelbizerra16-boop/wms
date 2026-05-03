from django.core.management.base import BaseCommand

from apps.nf.models import NotaFiscal
from apps.nf.services.status_service import atualizar_status_nf


class Command(BaseCommand):
    help = 'Recalcula e corrige o status operacional de todas as NFs.'

    def handle(self, *args, **options):
        total = 0
        for nf in NotaFiscal.objects.prefetch_related('itens', 'conferencias__itens').all():
            atualizar_status_nf(nf)
            self.stdout.write(f'NF {nf.numero}: {nf.status}')
            total += 1
        self.stdout.write(self.style.SUCCESS(f'{total} NF(s) recalculada(s).'))
