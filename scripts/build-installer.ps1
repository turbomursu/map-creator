param([string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $root 'release' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
Push-Location $root
try {
    & npm --prefix web run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    $version = (Get-Content 'web\package.json' -Raw | ConvertFrom-Json).version
    $name = "Fieldwork-$version-windows-x64"
    New-Item -ItemType Directory -Force $OutputDirectory | Out-Null
    $stage = Join-Path $OutputDirectory ("stage-" + [guid]::NewGuid().ToString('N'))
    $package = Join-Path $stage $name
    $payload = Join-Path $package 'payload'
    New-Item -ItemType Directory -Force "$payload\backend\app", "$payload\scripts", "$payload\web" | Out-Null
    try {
        Copy-Item 'backend\app\*.py' "$payload\backend\app"
        Copy-Item 'scripts\bootstrap.ps1', 'scripts\start.ps1' "$payload\scripts"
        Copy-Item 'tools', 'tiles' $payload -Recurse
        Copy-Item 'web\dist' "$payload\web" -Recurse
        Copy-Item 'pyproject.toml', 'README.md', 'THIRD_PARTY_NOTICES.md' $payload
        Copy-Item 'web\public\world.LICENSE.txt' "$payload\web"
        $licenses = New-Object System.Text.StringBuilder
        $production = & node -e "const fs=require('fs'); const lock=JSON.parse(fs.readFileSync('web\\package-lock.json','utf8')); console.log(JSON.stringify(Object.entries(lock.packages).filter(([name,p])=>name&&!p.dev).map(([name,p])=>({name,version:p.version,license:p.license}))));"
        if ($LASTEXITCODE -ne 0) { throw 'Could not read frontend dependency lock.' }
        foreach ($entry in ($production | ConvertFrom-Json | Sort-Object name)) {
            $dependency = Join-Path (Join-Path $root 'web') $entry.name
            [void]$licenses.AppendLine("=== $($entry.name) $($entry.version) ===")
            [void]$licenses.AppendLine("Declared license: $($entry.license)")
            $notices = @(Get-ChildItem $dependency -File | Where-Object { $_.Name -match '^(LICENSE|LICENCE|COPYING|NOTICE)' })
            if (-not $notices.Count -and $entry.name -eq 'node_modules/murmurhash-js') {
                $notices = @(Get-Item (Join-Path $dependency 'README.md'))
            }
            if (-not $notices.Count -and $entry.name -eq 'node_modules/pmtiles') {
                $notices = @(Get-Item 'tools\licenses\pmtiles-LICENSE.txt')
            }
            if (-not $notices.Count -and $entry.name -in @('node_modules/terra-draw', 'node_modules/terra-draw-maplibre-gl-adapter')) {
                $notices = @(Get-Item 'tools\licenses\terra-draw-LICENSE.txt')
            }
            if (-not $notices.Count) { throw "Missing distribution license notices: $($entry.name)" }
            foreach ($notice in $notices) {
                [void]$licenses.AppendLine([IO.File]::ReadAllText($notice.FullName))
            }
        }
        [IO.File]::WriteAllText("$payload\THIRD_PARTY_LICENSES.txt", $licenses.ToString())
        Copy-Item 'installer\*' $package
        $hashes = @(Get-ChildItem $payload -File -Recurse | Sort-Object FullName | ForEach-Object {
            [ordered]@{
                path = $_.FullName.Substring($payload.Length + 1)
                sha256 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash
            }
        })
        ConvertTo-Json -InputObject $hashes -Depth 3 | Set-Content "$package\payload-sha256.json" -Encoding UTF8
        $zip = Join-Path $OutputDirectory "$name.zip"
        Compress-Archive -Path $package -DestinationPath $zip -Force
        $digest = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant()
        "$digest  $name.zip" | Set-Content "$zip.sha256" -Encoding ASCII
        Write-Output "Installer: $zip"
        Write-Output "SHA-256: $digest"
    } finally {
        Remove-Item -LiteralPath $stage -Recurse -Force
    }
} finally {
    Pop-Location
}
