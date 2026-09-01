# Avvio MONOPROCESSO su Windows (PowerShell): backend + frontend built su :8787.
# Ricostruisce il frontend quando i sorgenti sono più recenti della build.
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$distIndex = Join-Path $root "frontend\dist\index.html"
$needsBuild = -not (Test-Path $distIndex)
if (-not $needsBuild) {
    $buildTime = (Get-Item $distIndex).LastWriteTimeUtc
    $inputs = @(
        (Join-Path $root "frontend\src"),
        (Join-Path $root "frontend\index.html"),
        (Join-Path $root "frontend\package.json"),
        (Join-Path $root "frontend\package-lock.json")
    )
    $newer = Get-ChildItem -Path $inputs -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTimeUtc -gt $buildTime } |
        Select-Object -First 1
    $needsBuild = $null -ne $newer
}
if ($env:TABULARIUM_BUILD_FRONTEND -eq "1") {
    $needsBuild = $true
}
if ($needsBuild) {
    Write-Host ">> frontend\dist assente o non aggiornato: eseguo la build..."
    & (Join-Path $PSScriptRoot "build_frontend.ps1")
}
Set-Location (Join-Path $root "backend")
$hostAddr = if ($env:TABULARIUM_HOST) { $env:TABULARIUM_HOST } else { "127.0.0.1" }
$port = if ($env:TABULARIUM_PORT) { $env:TABULARIUM_PORT } else { "8787" }
& ".venv\Scripts\uvicorn.exe" app.main:app --host $hostAddr --port $port
