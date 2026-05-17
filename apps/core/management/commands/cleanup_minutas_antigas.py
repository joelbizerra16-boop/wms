from django.core.management.base import BaseCommand

from apps.core.services.minuta_service import MINUTA_RETENCAO_DIAS, limpar_minutas_antigas


class Command(BaseCommand):
    help = 'Remove minutas e vínculos com mais de 10 dias (retenção operacional).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dias',
            type=int,
            default=MINUTA_RETENCAO_DIAS,
            help=f'Dias de retenção (padrão: {MINUTA_RETENCAO_DIAS}).',
        )

    def handle(self, *args, **options):
        removidos = limpar_minutas_antigas(dias=options['dias'])
        self.stdout.write(self.style.SUCCESS(f'Minutas removidas: {removidos}'))
