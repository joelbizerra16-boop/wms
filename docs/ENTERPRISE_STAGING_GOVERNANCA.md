# Governança staging enterprise — WMS

## Regra absoluta

- **Produção não é laboratório.**
- Migrations e load test sempre em **staging** antes de produção.
- Não commitar: `debug_queries.py`, `wms_db/`, logs `FILTRO_DEBUG` em produção.

## Alterações locais (auditoria)

| Artefato | Status | Ação |
|----------|--------|------|
| `usuarios/*` (login pocket) | Fora do repo | Revertido localmente; commit em PR dedicado quando validado |
| `operacional_cache.py` FILTRO_DEBUG | Removido | Mantido apenas `invalidate_setores_usuario_cache` (produção-safe) |
| `debug_queries.py` | Untracked | Apenas dev; listado no `.gitignore` |
| `wms_db/` | Untracked | Dados locais; nunca commitar |

## Migrations pendentes (enterprise)

Após `b6740c5`, aplicar em staging:

```bash
python manage.py migrate --plan
python manage.py migrate --noinput
python manage.py ensure_onda_brownfield_schema
```

Inclui (somente índices / brownfield — sem DROP destrutivo):

- `conferencia.0011_enterprise_db_indexes`
- `produtos.0007_enterprise_codigo_lookup`
- `tarefas.0016_onda_brownfield_postgresql`
- `tarefas.0017_enterprise_db_indexes`
- `logs.0006_log_created_at_brin_prep`

## Load test

```bash
python manage.py load_test_bipagem --synthetic --workers 50 --bipagens 10
python manage.py enterprise_staging_governanca --workers 20 --bipagens 5
```

Métricas: p50, p95, p99, throughput, metas documentadas no comando.

## Telemetria (staging)

- Logs: `BIPAGEM_TOTAL_MS`, `DB_QUERY_MS`, `DB_TRANSACTION_MS`, `CACHE_HIT`
- API gestor: `GET /api/telemetry/operacional/`

## Metas

| Métrica | Meta |
|---------|------|
| Bipagem p50 | < 80 ms |
| Bipagem p95 | < 180 ms |
| Bipagem p99 | < 300 ms |
| Cache hit | > 90% (produção com carga real) |

## Deploy produção (ordem)

1. Backup / snapshot DB
2. `migrate --plan` em staging idêntico
3. Janela: `migrate --noinput` + `ensure_onda_brownfield_schema`
4. Deploy app `b6740c5` ou posterior
5. Smoke: bipagem, separação, conferência, liberar NF
6. Monitorar `BIPAGEM_LENTA`, `DB_DEADLOCK`, 500

## Rollback

- App: redeploy commit anterior
- DB: índices novos são seguros (não remover em panic); brownfield onda via fallback clássico
