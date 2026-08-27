# Build del frontend su Windows (PowerShell) in frontend/dist.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\frontend")
npm run build
Write-Host ">> Frontend build in frontend/dist."