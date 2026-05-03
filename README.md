# WMS Base Django

Projeto base para um sistema WMS com Django, DRF e PostgreSQL, preparado para desenvolvimento e producao.

## Estrutura

```text
wms_001/
|-- apps/
|   |-- core/
|   |-- usuarios/
|   |-- produtos/
|   |-- clientes/
|   |-- rotas/
|   |-- nf/
|   |-- tarefas/
|   |-- conferencia/
|   |-- logs/
|-- config/
|   |-- settings/
|   |   |-- base.py
|   |   |-- dev.py
|   |   |-- prod.py
|   |-- urls.py
|   |-- asgi.py
|   |-- wsgi.py
|-- docker-compose.yml
|-- Dockerfile
|-- manage.py
|-- requirements.txt
|-- .env
|-- .env.example
|-- scripts/
```

## Ambiente local

**ATENCAO:** Este projeto utiliza exclusivamente o ambiente virtual `.venv`.

### Criacao do ambiente

Windows (PowerShell ou CMD):

```powershell
python -m venv .venv
```

Linux/Mac:

```bash
python3 -m venv .venv
```

### Ativacao

PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

CMD:

```bat
.venv\Scripts\activate
```

Linux/Mac:

```bash
source .venv/bin/activate
```

### Instalacao e execucao

```powershell
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py runserver
```

Por padrao, `config.settings.dev` usa SQLite local via `DEV_USE_SQLITE=True`, entao o `runserver` sobe sem PostgreSQL.

Para desenvolver com PostgreSQL local ou Docker, ajuste no `.env`:

```powershell
DEV_USE_SQLITE=False
DB_NAME=wms_db
DB_USER=wms_user
DB_PASSWORD=Wms@2026!Secure#Base
DB_HOST=localhost
DB_PORT=5432
```

## Comandos

```powershell
.\scripts\migrate.ps1
.\scripts\create_superuser.ps1
.\scripts\runserver.ps1
```

## Docker

```powershell
docker compose up --build
```

## Endpoints iniciais

- `GET /health/`
- `GET /swagger/`
- `GET /redoc/`

## Banco de dados

- Banco: `wms_db`
- Usuario: `wms_user`
- Timezone: `America/Sao_Paulo`
- Encoding: `UTF-8`