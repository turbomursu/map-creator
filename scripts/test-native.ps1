$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$env:PATH = "$root\.tools\runtime\Library\bin;$env:PATH"
$scratch = Join-Path $root '.tools\native fixture space'
New-Item -ItemType Directory -Force $scratch | Out-Null
& osmium cat "$root\tests\fixtures\border.osm" -o "$scratch\input.osm.pbf" --overwrite
if ($LASTEXITCODE -ne 0) { throw 'Fixture PBF conversion failed' }
foreach ($format in @('pmtiles', 'mbtiles')) {
    & java -Xmx1g -cp "$root\.tools\planetiler.jar" "$root\tiles\RegionProfile.java" `
        "--osm-path=$scratch\input.osm.pbf" "--aoi=$root\tests\fixtures\region.wkt" `
        "--output=$scratch\output.$format" "--tmpdir=$scratch\tmp-$format" `
        --auxiliary=false --only-layers=landcover,transportation --minzoom=12 --maxzoom=14 `
        --threads=2 --force
    if ($LASTEXITCODE -ne 0) { throw "Native $format generation failed" }
}
