# Setup ambiente frontend su Windows (PowerShell): node_modules.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\frontend")
npm ci
Write-Host ">> Frontend pronto."
