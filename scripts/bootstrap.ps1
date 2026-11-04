$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$tools = Join-Path $root '.tools'
New-Item -ItemType Directory -Force $tools | Out-Null
$mamba = Join-Path $tools 'micromamba.exe'
if (-not (Test-Path $mamba)) {
    Invoke-WebRequest 'https://github.com/mamba-org/micromamba-releases/releases/download/2.10.0-0/micromamba-win-64.exe' -UseBasicParsing -OutFile "$mamba.part"
    if ((Get-FileHash "$mamba.part" -Algorithm SHA256).Hash -ne 'F8B4284BD4EB1134122EAF93B3EA6C7BE567E596DE5829E1A6F478E34E41974A') {
        throw 'Micromamba checksum mismatch'
    }
    Move-Item "$mamba.part" $mamba
}
$env:MAMBA_ROOT_PREFIX = Join-Path $tools 'mamba'
$operation = if (Test-Path (Join-Path $tools 'runtime\conda-meta')) { 'install' } else { 'create' }
& $mamba $operation --yes --prefix (Join-Path $tools 'runtime') --file (Join-Path $root 'tools\win-64.lock')
if ($LASTEXITCODE -ne 0) { throw 'Native dependency installation failed' }
$jar = Join-Path $tools 'planetiler.jar'
if (-not (Test-Path $jar)) {
    $base = 'https://github.com/onthegomap/planetiler/releases/download/v0.10.2'
    Invoke-WebRequest "$base/planetiler.jar" -UseBasicParsing -OutFile "$jar.part"
    if ((Get-FileHash "$jar.part" -Algorithm SHA256).Hash -ne 'F310BD0413E2E4512B27F4046D418664E8E1D3BF31603C2A70E23DE06C167E4D') {
        throw 'Planetiler checksum mismatch'
    }
    if ((Get-FileHash $jar -Algorithm SHA256).Hash -ne 'F310BD0413E2E4512B27F4046D418664E8E1D3BF31603C2A70E23DE06C167E4D') {
        throw 'Existing Planetiler JAR checksum mismatch. Remove it and run bootstrap again.'
    }
    Move-Item "$jar.part" $jar
}
& $mamba run --prefix (Join-Path $tools 'runtime') osmium --version
if ($LASTEXITCODE -ne 0) { throw 'Native Osmium startup failed' }
& $mamba run --prefix (Join-Path $tools 'runtime') java -version
if ($LASTEXITCODE -ne 0) { throw 'Native Java startup failed' }
& (Join-Path $tools 'runtime\python.exe') -m pip install -e "$root[test]" -c (Join-Path $root 'tools\python.lock')
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
Push-Location (Join-Path $root 'web')
try {
    & npm ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally {
    Pop-Location
}
