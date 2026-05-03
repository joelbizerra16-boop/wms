from django.core.management.base import BaseCommand

from apps.core.services.produto_sync_service import sincronizar_produtos_relacionados


class Command(BaseCommand):
    help = 'Reprocessa vinculos de produtos em tarefas/conferencias e normaliza cadastro.'

    def handle(self, *args, **options):
        resultado = sincronizar_produtos_relacionados()

        self.stdout.write(self.style.SUCCESS('Sincronizacao de produtos concluida.'))
        self.stdout.write(f"Produtos normalizados: {resultado['produtos_normalizados']}")
        self.stdout.write(f"Itens de separacao corrigidos: {resultado['itens_tarefa_corrigidos']}")
        self.stdout.write(f"Itens de conferencia corrigidos: {resultado['itens_conferencia_corrigidos']}")
        self.stdout.write(f"Itens sem correspondencia (NAO_ENCONTRADO): {resultado['itens_nao_encontrados']}")
        self.stdout.write(f"EANs duplicados detectados: {len(resultado['eans_duplicados'])}")
