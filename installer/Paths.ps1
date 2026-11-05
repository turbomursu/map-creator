function Get-FieldworkPath([string]$Value, [string]$Label) {
    $Value = [Environment]::ExpandEnvironmentVariables($Value.Trim().Trim('"'))
    if ($Value -notmatch '^[A-Za-z]:\\' -or $Value -match '[*?<>|\[\]]' -or $Value.Substring(2).Contains(':')) {
        throw "$Label must be an absolute local-drive path, not a network, device or wildcard path."
    }
    $path = [IO.Path]::GetFullPath($Value).TrimEnd('\')
    if ($path -match '^[A-Za-z]:$' -or $path -eq [Environment]::GetFolderPath('UserProfile') -or
        $path -eq $env:LOCALAPPDATA -or $path -eq $env:SystemRoot) {
        throw "$Label must be a dedicated subdirectory, not a drive, profile or system root."
    }
    foreach ($segment in $path.Substring(3).Split('\')) {
        if ($segment -match '[ .]$' -or $segment -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)') {
            throw "$Label contains an unsupported Windows directory name."
        }
    }
    $ancestor = $path
    while ($ancestor) {
        if (Test-Path -LiteralPath $ancestor) {
            $item = Get-Item -LiteralPath $ancestor -Force
            if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "$Label must contain only real directories, not files, junctions or symbolic links."
            }
        }
        $parent = Split-Path $ancestor -Parent
        if ($parent -eq $ancestor) { break }
        $ancestor = $parent
    }
    return $path
}

function Test-FieldworkOverlap([string]$First, [string]$Second) {
    return $First.Equals($Second, [StringComparison]::OrdinalIgnoreCase) -or
        $First.StartsWith("$Second\", [StringComparison]::OrdinalIgnoreCase) -or
        $Second.StartsWith("$First\", [StringComparison]::OrdinalIgnoreCase)
}

function Test-FieldworkWritable([string]$Directory) {
    [IO.Directory]::CreateDirectory($Directory) | Out-Null
    $probe = Join-Path $Directory ('.fieldwork-write-' + [guid]::NewGuid().ToString('N'))
    try { [IO.File]::WriteAllText($probe, 'probe') }
    finally { if (Test-Path -LiteralPath $probe) { Remove-Item -LiteralPath $probe } }
}
