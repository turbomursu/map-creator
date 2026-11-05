param([int]$Port = 8765, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$settings = Join-Path $root 'fieldwork-settings.json'
if (-not $env:MAP_CREATOR_DATA -and (Test-Path -LiteralPath $settings)) {
    $configuration = Get-Content -LiteralPath $settings -Raw | ConvertFrom-Json
    if (-not $configuration.data_directory -or $configuration.data_directory -notmatch '^[A-Za-z]:\\') {
        throw 'Invalid saved map data directory in fieldwork-settings.json.'
    }
    $env:MAP_CREATOR_DATA = $configuration.data_directory
}
$python = Join-Path $root '.tools\runtime\python.exe'
if (-not (Test-Path $python)) { throw 'Run scripts\bootstrap.ps1 first.' }
if (-not (Test-Path (Join-Path $root 'web\dist\index.html'))) {
    throw 'Build the UI with npm --prefix web run build first.'
}
$env:PYTHONPATH = Join-Path $root 'backend'
$env:PATH = "$root\.tools\runtime\Library\bin;$env:PATH"
$browserJob = $null
if (-not $NoBrowser) {
    $browserJob = Start-Job -ArgumentList $Port -ScriptBlock {
        param($Port)
        $url = "http://127.0.0.1:$Port"
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            try {
                $response = Invoke-WebRequest "$url/api/session" -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
                if ($response.StatusCode -eq 200) { Start-Process $url; return }
            } catch [System.Net.WebException], [System.Net.Http.HttpRequestException], [System.OperationCanceledException] {
                Start-Sleep -Milliseconds 500
            }
        }
        Write-Warning "Server did not become ready. Open $url after resolving the startup error."
    }
}
try {
    & $python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Local server stopped with an error.' }
} finally {
    if ($browserJob) {
        Stop-Job $browserJob
        Receive-Job $browserJob
        Remove-Job $browserJob
    }
}
