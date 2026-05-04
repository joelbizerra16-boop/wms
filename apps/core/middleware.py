from django.http import HttpResponse


class CatchAllExceptionsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            response = self.get_response(request)
            return response
        except Exception as e:
            print(f"ERRO GLOBAL: {e}")
            return HttpResponse("Erro interno. Contate o suporte.")