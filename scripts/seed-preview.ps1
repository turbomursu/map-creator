$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (-not $env:MAP_CREATOR_DATA) { throw 'Set MAP_CREATOR_DATA to a dedicated test data directory first.' }
$env:PYTHONPATH = Join-Path $root 'backend'
@'
import json
import os
import shutil
from pathlib import Path
from app.artifacts import validate
from app.models import JobSpec
from app.store import Store

root = Path.cwd()
store = Store(Path(os.environ["MAP_CREATOR_DATA"]).resolve())
region = json.loads((root / "tests" / "fixtures" / "region.geojson").read_text())
for extension in ("pmtiles", "mbtiles"):
    source = root / ".tools" / "native fixture space" / f"output.{extension}"
    artifact = validate(source)
    spec = JobSpec(name=f"Native fixture {extension}", geometry=region, format=extension,
                   minzoom=12, maxzoom=14, layers=["landcover", "transportation"])
    job = store.create(spec.model_dump(), {"sources": []})
    directory = store.directory(job["id"])
    shutil.copyfile(source, directory / f"map.{extension}")
    (directory / "manifest.json").write_text(json.dumps({
        "attribution": artifact["metadata"]["attribution"], "artifact": artifact}), encoding="utf-8")
    store.update(job["id"], "ready")
print("Validated native preview fixtures are ready.")
'@ | & (Join-Path $root '.tools\runtime\python.exe') -
if ($LASTEXITCODE -ne 0) { throw 'Preview fixture seeding failed' }
