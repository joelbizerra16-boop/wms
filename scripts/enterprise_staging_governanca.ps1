# Runbook staging enterprise — NÃO executar em produção sem janela aprovada.
# Uso: .\scripts\enterprise_staging_governanca.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

Write-Host "=== Governança WMS — staging/local ===" -ForegroundColor Cyan

python manage.py showmigrations --plan | Select-String "\[ \]"
Write-Host "`n=== migrate --plan ===" -ForegroundColor Cyan
python manage.py migrate --plan

$confirm = Read-Host "Aplicar migrate neste ambiente? (s/N)"
if ($confirm -eq 's') {
    python manage.py migrate --noinput
    python manage.py ensure_onda_brownfield_schema
}

python manage.py enterprise_staging_governanca --workers 20 --bipagens 5
Write-Host "`nConcluído. Ver docs/ENTERPRISE_STAGING_GOVERNANCA.md" -ForegroundColor Green
