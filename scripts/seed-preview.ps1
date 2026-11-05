$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (-not $env:MAP_CREATOR_DATA) { throw 'Set MAP_CREATOR_DATA to a dedicated test data directory first.' }
$env:PYTHONPATH = Join-Path $root 'backend'
@'
import json
import os
import shutil
import subprocess
from pathlib import Path
from app.artifacts import validate
from app.models import JobSpec
from app.store import Store
from shapely.geometry import box, mapping
from shapely.ops import unary_union

root = Path.cwd()
store = Store(Path(os.environ["MAP_CREATOR_DATA"]).resolve())
source = root / ".tools" / "native fixture space" / "input.osm.pbf"
native = root / ".tools" / "runtime" / "Library" / "bin"
info = json.loads(subprocess.check_output(
    [str(native / "osmium.exe"), "fileinfo", "--json", str(source)],
    env={**os.environ, "PATH": str(native) + os.pathsep + os.environ["PATH"]}))
stat = source.stat()
local = {"id": "local-browser-fixture", "name": source.name, "local_path": str(source),
         "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
         "coverage": mapping(unary_union([box(*bounds) for bounds in info["header"]["boxes"]]))}
(store.root / "local-inputs.json").write_text(json.dumps({local["id"]: local}), encoding="utf-8")
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
