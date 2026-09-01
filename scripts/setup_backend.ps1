# Setup ambiente backend su Windows (PowerShell): venv + dipendenze.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\backend")
$py = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$pyVersion = (& $py -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')").Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Python non trovato: $py (imposta PYTHON sul percorso di Python 3.11, 3.12 o 3.13)"
}
if ($pyVersion -notin @("3.11", "3.12", "3.13")) {
    throw "Versione Python non supportata: $pyVersion (richiesta 3.11–3.13)"
}

if (-not (Test-Path ".venv")) {
    Write-Host ">> Creo virtualenv..."
    & $py -m venv .venv
}
& ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-dev.txt
& ".venv\Scripts\python.exe" -c "import cryptography; print('cryptography ' + cryptography.__version__)"
if ($LASTEXITCODE -ne 0) {
    throw "Dipendenza cryptography non disponibile: il vault dei segreti non è sicuro."
}
Write-Host ">> Backend pronto."
