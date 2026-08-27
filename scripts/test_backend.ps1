$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..\backend')
if (-not (Test-Path '.venv\Scripts\python.exe')) {
  Write-Error 'backend/.venv non trovato: esegui scripts/setup_backend.ps1'
}
& '.venv\Scripts\python.exe' -m pytest @args
