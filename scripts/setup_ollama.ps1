$ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
if (-not (Test-Path $ollama)) {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { $ollama = $cmd.Source }
}
if (-not (Test-Path $ollama)) {
    Write-Host "Installing Ollama (official Windows app)..." -ForegroundColor Cyan
    winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
    $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
}
$model = "qwen2.5:7b"
$envFile = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
if (Test-Path $envFile) {
    $line = Select-String -Path $envFile -Pattern "^OLLAMA_MODEL=(.+)$" | Select-Object -First 1
    if ($line) { $model = $line.Matches[0].Groups[1].Value.Trim() }
}
Write-Host "Downloading model $model (about 4.7 GB for qwen2.5:7b)..." -ForegroundColor Cyan
& $ollama pull $model
Write-Host "Done. Ollama runs in the background (tray icon). The app detects it automatically (AI_PROVIDER=auto)." -ForegroundColor Green
