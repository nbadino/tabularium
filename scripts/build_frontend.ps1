# Build del frontend su Windows (PowerShell) in frontend/dist.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\frontend")
npm run build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host ">> Frontend build in frontend/dist."