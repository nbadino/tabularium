# Avvio backend API su Windows (PowerShell) su http://127.0.0.1:8787.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\backend")
$hostAddr = if ($env:TABULARIUM_HOST) { $env:TABULARIUM_HOST } else { "127.0.0.1" }
$port = if ($env:TABULARIUM_PORT) { $env:TABULARIUM_PORT } else { "8787" }
& ".venv\Scripts\uvicorn.exe" app.main:app --host $hostAddr --port $port --reload