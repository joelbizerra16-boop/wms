from rest_framework import status, viewsets
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.nf.models import NotaFiscal, NotaFiscalItem
from apps.nf.serializers import NotaFiscalItemSerializer, NotaFiscalSerializer, XMLImportacaoSerializer
from apps.nf.services.importador_xml import ImportacaoXMLError, importar_xml_nfe


class NotaFiscalViewSet(viewsets.ModelViewSet):
    serializer_class = NotaFiscalSerializer
    filterset_fields = ('status', 'status_fiscal', 'bloqueada', 'ativa', 'cliente', 'rota')
    search_fields = ('numero', 'chave_nfe', 'cliente__nome', 'rota__nome')
    ordering_fields = ('numero', 'data_emissao', 'created_at', 'updated_at')

    def get_queryset(self):
        return NotaFiscal.objects.select_related('cliente', 'rota').prefetch_related('itens__produto').order_by('-data_emissao')


class NotaFiscalItemViewSet(viewsets.ModelViewSet):
    serializer_class = NotaFiscalItemSerializer
    filterset_fields = ('nf', 'produto')
    search_fields = ('nf__numero', 'produto__cod_prod', 'produto__descricao')
    ordering_fields = ('nf', 'produto', 'created_at', 'updated_at')

    def get_queryset(self):
        return NotaFiscalItem.objects.select_related('nf', 'produto').order_by('nf_id', 'produto_id')


class ImportarXMLAPIView(APIView):
    parser_classes = (MultiPartParser, FormParser)
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = XMLImportacaoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            resultado = importar_xml_nfe(
                serializer.validated_data['file'],
                usuario=request.user,
                balcao=request.data.get('balcao') in {'1', 'on', 'true', 'True'},
                tarefas_lote_cache={},
            )
        except ImportacaoXMLError as exc:
            return Response(
                {
                    'sucesso': False,
                    'erros': [str(exc)],
                    'quantidade_itens_importados': 0,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(resultado, status=status.HTTP_200_OK)
