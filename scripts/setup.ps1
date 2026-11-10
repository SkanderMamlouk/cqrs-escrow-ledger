# One-time installation on a Windows PC (called by SETUP.bat).
# 1. finds Python 3.11-3.13   2. creates the .venv folder   3. installs packages
# 4. creates .env              5. creates tables              6. loads demo data
# 7. runs the verification gate
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
Write-Host ""
Write-Host "=== CQRS & Event Sourcing Ledger - setup ===" -ForegroundColor Cyan
Write-Host "Project folder: $root"

function Fail($msg) { Write-Host ""; Write-Host "SETUP FAILED: $msg" -ForegroundColor Red; exit 1 }

# ---- 1. Python (3.12 or 3.11: the embedded PostgreSQL exists for these) ----
function Find-Python {
    foreach ($cand in @("py -3.12", "py -3.11", "python")) {
        $out = cmd /c "$cand --version 2>&1"
        if ($LASTEXITCODE -eq 0 -and "$out" -match "Python 3\.(11|12)\.") { return $cand }
    }
    $local = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    if (Test-Path $local) { return "`"$local`"" }
    return $null
}
$py = Find-Python
if (-not $py) {
    Write-Host "Python 3.12 not found - installing it with winget (accept the prompts)..." -ForegroundColor Yellow
    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
    $py = Find-Python
}
if (-not $py) {
    Fail "Python 3.12 is required. Install it with:  winget install Python.Python.3.12  (or from python.org - tick 'Add python.exe to PATH'), then run SETUP.bat again."
}
Write-Host "[1/7] Using $py ($(cmd /c "$py --version"))" -ForegroundColor Green

# ---- 2. virtual environment -------------------------------------------------
$vpy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) {
    Write-Host "[2/7] Creating virtual environment .venv ..."
    cmd /c "$py -m venv `"$root\.venv`""
    if (-not (Test-Path $vpy)) { Fail "could not create .venv" }
} else { Write-Host "[2/7] .venv already exists" -ForegroundColor Green }

# ---- 3. packages ------------------------------------------------------------
Write-Host "[3/7] Installing Python packages (first time: 2-5 minutes)..."
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) { Fail "pip install failed (check your internet connection)" }

# ---- 4. .env ------------------------------------------------------------------
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "[4/7] Created .env from .env.example" }
else { Write-Host "[4/7] .env already exists (kept)" -ForegroundColor Green }

# ---- 5. tables ----------------------------------------------------------------
Write-Host "[5/7] Starting the database and creating read-model tables..."
& $vpy manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { Fail "migrate failed" }

# ---- 6. demo data -------------------------------------------------------------
Write-Host "[6/7] Loading synthetic demo data (only if the event store is empty)..."
& $vpy manage.py seed_demo
if ($LASTEXITCODE -ne 0) { Fail "seed_demo failed" }

# ---- 7. verification gate ------------------------------------------------------
Write-Host "[7/7] Running the deterministic verification gate..."
& $vpy manage.py verify_ledger
if ($LASTEXITCODE -ne 0) { Fail "verification gate failed" }

Write-Host ""
Write-Host "SETUP COMPLETE. Double-click START.bat to launch the application." -ForegroundColor Green
