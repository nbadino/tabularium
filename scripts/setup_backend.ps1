# Setup ambiente backend su Windows (PowerShell): venv + dipendenze.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\backend")
$py = if ($env:PYTHON) { $env:PYTHON } else { "python" }

if (-not (Test-Path ".venv")) {
    Write-Host ">> Creo virtualenv..."
    & $py -m venv .venv
}
& ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-dev.txt
Write-Host ">> Backend pronto."