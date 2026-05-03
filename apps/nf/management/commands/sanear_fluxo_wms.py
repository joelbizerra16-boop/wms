from django.core.management.base import BaseCommand

from apps.nf.services.consistencia_service import sanear_consistencia_fluxo


class Command(BaseCommand):
    help = 'Executa saneamento de consistencia no fluxo NF -> Separacao -> Conferencia.'

    def handle(self, *args, **options):
        resultado = sanear_consistencia_fluxo()
        self.stdout.write(
            self.style.SUCCESS(
                f"Saneamento concluido. NFs avaliadas: {resultado['total']}. Inconsistentes: {resultado['inconsistentes']}."
            )
        )
