# Avvio backend API su Windows (PowerShell) su http://127.0.0.1:8787.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\backend")
# Host and port from .env, as run.sh does (variables already set win).
$envFile = Join-Path $PSScriptRoot "..\.env"
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*(TABULARIUM_HOST|TABULARIUM_PORT)\s*=\s*"?([^"#]*?)"?\s*$' -and -not (Test-Path "env:$($Matches[1])")) {
            Set-Item "env:$($Matches[1])" $Matches[2]
        }
    }
}
$hostAddr = if ($env:TABULARIUM_HOST) { $env:TABULARIUM_HOST } else { "127.0.0.1" }
$port = if ($env:TABULARIUM_PORT) { $env:TABULARIUM_PORT } else { "8787" }
& ".venv\Scripts\uvicorn.exe" app.main:app --host $hostAddr --port $port --reload