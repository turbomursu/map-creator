param([switch]$ConfirmRemoval)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$record = Join-Path $root 'installation.json'
if (-not (Test-Path -LiteralPath $record) -or -not (Test-Path -LiteralPath "$root\Paths.ps1")) {
    throw 'Run the installed copy of Uninstall.ps1, not the installer package copy.'
}
. (Join-Path $root 'Paths.ps1')
$root = Get-FieldworkPath $root 'Application directory'
$installation = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
if ($installation.root -ne $root -or -not $installation.files.Count) {
    throw 'Installation ownership record does not match this directory.'
}
$data = Get-FieldworkPath $installation.data_directory 'Map data directory'
if (Test-FieldworkOverlap $root $data) { throw 'Refusing uninstall: map data overlaps application files.' }
$paths = @{}
$ownedDirectories = @{}
foreach ($entry in $installation.files) {
    $path = [IO.Path]::GetFullPath((Join-Path $root $entry.path))
    if (-not $path.StartsWith("$root\", [StringComparison]::OrdinalIgnoreCase) -or $paths.ContainsKey($path)) {
        throw 'Invalid installation ownership path.'
    }
    $paths[$path] = $entry.sha256
    $parent = Split-Path $path -Parent
    while ($parent -ne $root) {
        $ownedDirectories[$parent] = $true
        $parent = Split-Path $parent -Parent
    }
}
# Never traverse a junction or symbolic link while removing recorded files.
$pending = New-Object 'System.Collections.Generic.Queue[string]'
$pending.Enqueue($root)
$directories = @()
while ($pending.Count) {
    $directory = $pending.Dequeue()
    foreach ($item in Get-ChildItem -LiteralPath $directory -Force) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Remove application-directory junctions/symbolic links before uninstalling.'
        }
        if ($item.PSIsContainer) { $directories += $item.FullName; $pending.Enqueue($item.FullName) }
    }
}
if (-not $ConfirmRemoval -and (Read-Host "Remove Fieldwork at $root? Data at $data will be kept. Type REMOVE") -cne 'REMOVE') {
    Write-Output 'Uninstall cancelled.'
    return
}
$running = Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith("$root\", [StringComparison]::OrdinalIgnoreCase)
}
if ($running) { throw 'Close Fieldwork and all terminals using its runtime before uninstalling.' }
$link = Join-Path ([Environment]::GetFolderPath('Programs')) 'Fieldwork.lnk'
if ($installation.shortcut -eq $link -and (Test-Path -LiteralPath $link)) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($link)
    $expected = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $root 'scripts\start.ps1') + '"'
    if ($shortcut.Arguments -eq $expected -and
        $shortcut.TargetPath -eq "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe") {
        Remove-Item -LiteralPath $link
    }
}
$preserved = 0
# Keep the uninstaller and its helper available if a locked file interrupts removal.
$orderedPaths = $paths.Keys | Sort-Object @{
    Expression = { if ($_ -in @("$root\Uninstall.ps1", "$root\Paths.ps1")) { 1 } else { 0 } }
}
foreach ($path in $orderedPaths) {
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -eq $paths[$path]) {
            Remove-Item -LiteralPath $path -Force
        } else { $preserved++ }
    }
}
foreach ($directory in ($directories | Sort-Object Length -Descending)) {
    if ($ownedDirectories.ContainsKey($directory) -and
        -not @(Get-ChildItem -LiteralPath $directory -Force).Count) {
        Remove-Item -LiteralPath $directory
    }
}
Remove-Item -LiteralPath $record
Write-Output "Application removed. Modified files preserved: $preserved. Additional user files are retained in $root."
Write-Output "Map data remains at $data. The installation folder is retained."
