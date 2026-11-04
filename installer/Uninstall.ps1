param([switch]$ConfirmRemoval)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if (-not (Test-Path "$root\scripts\start.ps1") -or -not (Test-Path "$root\pyproject.toml")) {
    throw 'Run the installed copy of Uninstall.ps1, not the installer package copy.'
}
if (-not $ConfirmRemoval -and (Read-Host "Remove Fieldwork application at $root? Map data will be kept. Type REMOVE") -cne 'REMOVE') {
    Write-Output 'Uninstall cancelled.'
    return
}
$running = Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith("$root\", [StringComparison]::OrdinalIgnoreCase)
}
if ($running) { throw 'Close Fieldwork and all terminals using its runtime before uninstalling.' }
$link = Join-Path ([Environment]::GetFolderPath('Programs')) 'Fieldwork.lnk'
if (Test-Path $link) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($link)
    if ($shortcut.Arguments.Contains($root)) { Remove-Item -LiteralPath $link }
}
# Delete only installer-owned paths; preserve user-created files and the separate map data directory.
foreach ($name in @('.tools', 'backend', 'scripts', 'tools', 'tiles', 'web')) {
    $path = Join-Path $root $name
    if (Test-Path $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}
foreach ($name in @('pyproject.toml', 'README.md', 'THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_LICENSES.txt', 'INSTALL.txt', 'install.log', 'Uninstall.ps1')) {
    $path = Join-Path $root $name
    if (Test-Path $path) { Remove-Item -LiteralPath $path -Force }
}
Write-Output "Application removed. Empty installation folder retained: $root"
Write-Output 'Map data remains in %LOCALAPPDATA%\MapCreator (or your MAP_CREATOR_DATA directory).'
