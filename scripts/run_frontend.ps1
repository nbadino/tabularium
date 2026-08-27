# Avvio frontend dev su Windows (PowerShell) (Vite, proxy /api -> 127.0.0.1:8787).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\frontend")
npm run dev