from django.core.management.base import BaseCommand

from apps.nf.models import EntradaNF
from apps.nf.services.xml_storage_service import ensure_entrada_xml_available


class Command(BaseCommand):
    help = 'Sincroniza XMLs cadastrados em EntradaNF com o storage atual e tenta recuperar arquivos legados locais.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=0, help='Limita a quantidade de entradas processadas.')
        parser.add_argument(
            '--only-missing',
            action='store_true',
            help='Processa apenas entradas cujo arquivo nao existe no storage atual.',
        )

    def handle(self, *args, **options):
        entradas = EntradaNF.objects.exclude(xml='').order_by('id')
        limit = options['limit'] or 0
        if limit > 0:
            entradas = entradas[:limit]

        verificadas = 0
        sincronizadas = 0
        ausentes = 0
        for entrada in entradas:
            xml_name = (getattr(entrada.xml, 'name', '') or '').strip()
            if not xml_name:
                continue
            try:
                exists = entrada.xml.storage.exists(xml_name)
            except Exception:
                exists = False
            if options['only_missing'] and exists:
                continue

            verificadas += 1
            ok = ensure_entrada_xml_available(entrada)
            if ok:
                if not exists:
                    sincronizadas += 1
                    self.stdout.write(self.style.SUCCESS(f'Entrada {entrada.id}: XML sincronizado ({xml_name}).'))
            else:
                ausentes += 1
                self.stdout.write(self.style.WARNING(f'Entrada {entrada.id}: XML indisponivel ({xml_name}).'))

        self.stdout.write(self.style.SUCCESS(f'Entradas verificadas: {verificadas}'))
        self.stdout.write(self.style.SUCCESS(f'XMLs sincronizados: {sincronizadas}'))
        self.stdout.write(self.style.WARNING(f'XMLs ainda ausentes: {ausentes}'))