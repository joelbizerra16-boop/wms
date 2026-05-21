"""Runbook: migrations + migrate plan + load test enterprise (staging/local)."""

import json
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Auditoria de migrations e benchmark operacional (não usar em produção como carga).'

    def add_arguments(self, parser):
        parser.add_argument('--skip-migrate', action='store_true', help='Apenas plano, sem aplicar migrate')
        parser.add_argument('--workers', type=int, default=20)
        parser.add_argument('--bipagens', type=int, default=5)
        parser.add_argument('--output', default='docs/benchmark_ultimo.json')

    def handle(self, *args, **options):
        repo = Path(settings.BASE_DIR)
        self.stdout.write(self.style.MIGRATE_HEADING('=== FASE 2: showmigrations (pendentes) ==='))
        out = StringIO()
        call_command('showmigrations', '--plan', stdout=out)
        plan_text = out.getvalue()
        pendentes = [linha for linha in plan_text.splitlines() if '[ ]' in linha]
        self.stdout.write(f'Pendentes: {len(pendentes)}')
        for linha in pendentes[:30]:
            self.stdout.write(linha)
        if len(pendentes) > 30:
            self.stdout.write(f'... +{len(pendentes) - 30}')

        self.stdout.write(self.style.MIGRATE_HEADING('=== FASE 4: migrate --plan ==='))
        out = StringIO()
        call_command('migrate', '--plan', stdout=out)
        migrate_plan = out.getvalue()
        self.stdout.write(migrate_plan)

        if not options['skip_migrate']:
            self.stdout.write(self.style.MIGRATE_HEADING('=== migrate --noinput (ambiente atual) ==='))
            call_command('migrate', '--noinput', verbosity=1)
            try:
                call_command('ensure_onda_brownfield_schema', verbosity=1)
            except Exception as exc:
                self.stderr.write(f'ensure_onda_brownfield_schema: {exc}')

        self.stdout.write(self.style.MIGRATE_HEADING('=== FASE 5: load_test_bipagem (sintético) ==='))
        lt_out = StringIO()
        call_command(
            'load_test_bipagem',
            '--synthetic',
            workers=options['workers'],
            bipagens=options['bipagens'],
            stdout=lt_out,
        )
        self.stdout.write(lt_out.getvalue())

        relatorio = {
            'migrations_pendentes': len(pendentes),
            'migrate_plan_excerpt': migrate_plan[-4000:],
            'load_test': lt_out.getvalue().strip(),
        }
        output_path = repo / options['output']
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(f'Relatório: {output_path}'))
