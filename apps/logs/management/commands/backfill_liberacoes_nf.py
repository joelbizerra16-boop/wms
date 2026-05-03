from django.core.management.base import BaseCommand

from apps.logs.models import LiberacaoDivergencia
from apps.nf.models import NotaFiscal


class Command(BaseCommand):
    help = 'Preenche NF e nf_numero em liberações antigas sem vínculo de NF.'

    def handle(self, *args, **options):
        atualizados = 0
        ignorados = 0

        for liberacao in LiberacaoDivergencia.objects.select_related('tarefa__nf').filter(nf__isnull=True):
            nf = None
            if liberacao.tarefa_id and liberacao.tarefa and liberacao.tarefa.nf_id:
                nf = liberacao.tarefa.nf
            elif liberacao.nf_numero:
                nf = NotaFiscal.objects.filter(numero=liberacao.nf_numero).first()

            if nf is None:
                ignorados += 1
                continue

            liberacao.nf = nf
            if not liberacao.nf_numero:
                liberacao.nf_numero = nf.numero
                liberacao.save(update_fields=['nf', 'nf_numero', 'updated_at'])
            else:
                liberacao.save(update_fields=['nf', 'updated_at'])
            atualizados += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Backfill concluído. Atualizados: {atualizados}. Ignorados sem vínculo: {ignorados}.'
            )
        )
