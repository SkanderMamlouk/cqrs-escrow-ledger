# Launches the 2 processes of the application (called by START.bat):
#   window 1 (new)     : the PROJECTOR worker  (event log -> read model, webhooks)
#   window 2 (this one): the WEB SERVER        http://127.0.0.1:8000
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$vpy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) {
    Write-Host "First launch: running setup..." -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot "setup.ps1")
    if ($LASTEXITCODE -ne 0) { exit 1 }
}
Write-Host "=== Starting CQRS ledger ===" -ForegroundColor Cyan
& $vpy manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { Write-Host "Database migration failed (see the message above). Try SETUP.bat again." -ForegroundColor Red; exit 1 }

Write-Host "Starting the projector worker in a second window..."
Start-Process -FilePath $vpy -ArgumentList "manage.py", "run_projector" -WorkingDirectory $root

Start-Job -ScriptBlock { Start-Sleep 4; Start-Process "http://127.0.0.1:8000/" } | Out-Null
Write-Host "Web server: http://127.0.0.1:8000   (press CTRL+C here to stop, then close the projector window)" -ForegroundColor Green
& $vpy manage.py runserver 127.0.0.1:8000 --noreload
