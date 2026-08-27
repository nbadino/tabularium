# Setup ambiente frontend su Windows (PowerShell): node_modules.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\frontend")
npm install
Write-Host ">> Frontend pronto."