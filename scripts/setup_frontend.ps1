# Setup ambiente frontend su Windows (PowerShell): node_modules.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\frontend")
npm ci
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host ">> Frontend pronto."
