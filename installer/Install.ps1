param(
    [string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'Fieldwork'),
    [string]$DataDirectory = (Join-Path $env:LOCALAPPDATA 'MapCreator'),
    [switch]$NonInteractive,
    [switch]$NoShortcuts
)
$ErrorActionPreference = 'Stop'
if (-not [Environment]::Is64BitProcess -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    throw 'Run this installer using 64-bit Windows PowerShell on x64 Windows.'
}
. (Join-Path $PSScriptRoot 'Paths.ps1')
if (-not $NonInteractive) {
    if (-not $PSBoundParameters.ContainsKey('InstallDirectory')) {
        $answer = Read-Host "Application directory [$InstallDirectory]"
        if ($answer.Trim()) { $InstallDirectory = $answer }
    }
    if (-not $PSBoundParameters.ContainsKey('DataDirectory')) {
        $answer = Read-Host "Map data directory [$DataDirectory]"
        if ($answer.Trim()) { $DataDirectory = $answer }
    }
}
$InstallDirectory = Get-FieldworkPath $InstallDirectory 'Application directory'
$DataDirectory = Get-FieldworkPath $DataDirectory 'Map data directory'
if (Test-FieldworkOverlap $InstallDirectory $DataDirectory) {
    throw 'Application and map data directories must be separate, without either containing the other.'
}
if (Test-FieldworkOverlap $InstallDirectory $PSScriptRoot) {
    throw 'Application directory must be outside the extracted installer directory.'
}
if ($InstallDirectory -match '[^\x20-\x7E]') {
    throw 'The native tool installation path must use ASCII characters. Rerun with -InstallDirectory C:\Fieldwork (or another writable ASCII path). Data and imported filenames may use Unicode.'
}
if ((Test-Path -LiteralPath $InstallDirectory) -and
    @(Get-ChildItem -LiteralPath $InstallDirectory -Force).Count) {
    throw "Installation directory already exists: $InstallDirectory. Close Fieldwork and uninstall that copy first, or choose another empty directory. Map data will be preserved."
}
$payload = Join-Path $PSScriptRoot 'payload'
$entries = Get-Content (Join-Path $PSScriptRoot 'payload-sha256.json') -Raw | ConvertFrom-Json
$files = @(Get-ChildItem $payload -File -Recurse)
if (-not $entries.Count -or $files.Count -ne $entries.Count) { throw 'Installer payload file count mismatch.' }
$seen = @{}
foreach ($entry in $entries) {
    $file = [IO.Path]::GetFullPath((Join-Path $payload $entry.path))
    if (-not $file.StartsWith("$payload\", [StringComparison]::OrdinalIgnoreCase) -or $seen.ContainsKey($file)) {
        throw 'Invalid installer manifest path.'
    }
    $seen[$file] = $true
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $entry.sha256) {
        throw "Installer payload checksum mismatch: $($entry.path)"
    }
}
$link = Join-Path ([Environment]::GetFolderPath('Programs')) 'Fieldwork.lnk'
if (-not $NoShortcuts -and (Test-Path -LiteralPath $link)) {
    throw 'A Fieldwork Start menu shortcut already exists. Uninstall its owner or use -NoShortcuts.'
}
Test-FieldworkWritable $DataDirectory
Test-FieldworkWritable $InstallDirectory
$log = Join-Path $InstallDirectory 'install.log'
Start-Transcript -Path $log | Out-Null
try {
    Copy-Item (Join-Path $PSScriptRoot 'Uninstall.ps1') $InstallDirectory
    Copy-Item (Join-Path $PSScriptRoot 'Paths.ps1') $InstallDirectory
    Copy-Item (Join-Path $PSScriptRoot 'INSTALL.txt') $InstallDirectory
    Copy-Item "$payload\*" $InstallDirectory -Recurse
    @{data_directory = $DataDirectory} | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $InstallDirectory 'fieldwork-settings.json') -Encoding UTF8
    & (Join-Path $InstallDirectory 'scripts\bootstrap.ps1') -RuntimeOnly
    if (-not $NoShortcuts) {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($link)
        $shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
        $shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $InstallDirectory 'scripts\start.ps1') + '"'
        $shortcut.WorkingDirectory = $InstallDirectory
        $shortcut.Description = 'Fieldwork - local OSM vector tile workshop'
        $shortcut.Save()
    }
    Write-Output "Installed Fieldwork in $InstallDirectory"
    Write-Output "Map data directory: $DataDirectory"
    Write-Output 'Launch from the Start menu, or run scripts\start.ps1 in the installation directory.'
    Write-Output 'Keep the server terminal open while using the app. See INSTALL.txt for limitations.'
} catch {
    Write-Warning "Installation did not finish. Details: $log. The partial directory is retained for diagnosis; remove it with Uninstall.ps1 before retrying."
    throw
} finally {
    Stop-Transcript | Out-Null
    $files = @(Get-ChildItem -LiteralPath $InstallDirectory -File -Recurse -Force | ForEach-Object {
        if ($_.Name -ne 'installation.json') {
            @{path = $_.FullName.Substring($InstallDirectory.Length + 1)
              sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}
        }
    })
    @{root = $InstallDirectory; data_directory = $DataDirectory; files = $files
      shortcut = $(if (-not $NoShortcuts -and (Test-Path -LiteralPath $link)) { $link } else { $null })} |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $InstallDirectory 'installation.json') -Encoding UTF8
}
