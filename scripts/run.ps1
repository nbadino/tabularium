# Avvio MONOPROCESSO su Windows (PowerShell): backend + frontend built su :8787.
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not (Test-Path (Join-Path $root "frontend\dist\index.html"))) {
    Write-Host ">> frontend\dist assente: eseguo la build..."
    & (Join-Path $PSScriptRoot "build_frontend.ps1")
}
Set-Location (Join-Path $root "backend")
$hostAddr = if ($env:LLOYDS_HOST) { $env:LLOYDS_HOST } else { "127.0.0.1" }
$port = if ($env:LLOYDS_PORT) { $env:LLOYDS_PORT } else { "8787" }
& ".venv\Scripts\uvicorn.exe" app.main:app --host $hostAddr --port $port