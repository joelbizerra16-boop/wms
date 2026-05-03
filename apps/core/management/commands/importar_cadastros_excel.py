from django.core.management.base import BaseCommand, CommandError

from apps.core.services.cadastro_import_service import importar_cadastros


class Command(BaseCommand):
    help = 'Importa produtos e clientes a partir das planilhas Excel.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--produtos',
            dest='planilha_produtos',
            help='Caminho da planilha de produtos (default: CAD_PROD.xlsx no projeto).',
        )
        parser.add_argument(
            '--clientes',
            dest='planilha_clientes',
            help='Caminho da planilha de clientes (default: PRACA.xls no projeto).',
        )

    def handle(self, *args, **options):
        try:
            resultado = importar_cadastros(
                planilha_produtos=options.get('planilha_produtos'),
                planilha_clientes=options.get('planilha_clientes'),
            )
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS('Importacao concluida com sucesso.'))
        self.stdout.write(f"Produtos: {resultado['planilha_produtos']}")
        self.stdout.write(
            f"  criados={resultado['produtos']['criados']} "
            f"atualizados={resultado['produtos']['atualizados']} "
            f"ignorados={resultado['produtos']['ignorados']}"
        )
        self.stdout.write(f"Clientes: {resultado['planilha_clientes']}")
        self.stdout.write(
            f"  criados={resultado['clientes']['criados']} "
            f"atualizados={resultado['clientes']['atualizados']} "
            f"ignorados={resultado['clientes']['ignorados']}"
        )
