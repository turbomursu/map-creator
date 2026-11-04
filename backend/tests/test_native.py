import json
import math
import os
import subprocess
from pathlib import Path

import pytest
from app.artifacts import decode_tile, tiles, validate
from app.geometry import normalize_region
from shapely import affinity
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / ".tools" / "native fixture space"


@pytest.mark.skipif(not (FIXTURE / "output.pmtiles").exists(), reason="Run scripts\\test-native.ps1")
def test_native_formats_and_exact_polygon_clip():
    region = shape(json.loads((ROOT / "tests" / "fixtures" / "region.geojson").read_text()))
    signatures = []
    for extension in ("pmtiles", "mbtiles"):
        path = FIXTURE / f"output.{extension}"
        validate(path)
        decoded = {}
        feature_count = 0
        for (z, x, y), payload in tiles(path):
            tile = decode_tile(payload)
            decoded[z, x, y] = tile
            for layer in tile.values():
                extent = layer["extent"]
                scale = (1 << z) * extent
                def mercator(position, scale=scale, x=x, y=y, extent=extent):
                    lon, lat = position
                    return ((lon + 180) / 360 * scale - x * extent,
                            (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2
                            * scale - y * extent)
                from shapely.ops import transform
                mask = transform(lambda lon, lat: mercator((lon, lat)), region)
                for feature in layer["features"]:
                    # The decoder returns y-up coordinates; the source AOI uses screen coordinates.
                    geometry = affinity.affine_transform(shape(feature["geometry"]),
                                                         [1, 0, 0, -1, 0, extent])
                    assert mask.buffer(1.5).covers(geometry)
                    feature_count += 1
        assert feature_count > 10
        signatures.append(decoded)
    assert signatures[0] == signatures[1]


@pytest.mark.skipif(not (ROOT / ".tools" / "runtime" / "Library" / "bin" / "osmium.exe").exists(),
                    reason="Native Osmium unavailable")
def test_native_merge_deduplicates_and_extracts_complete_ways(tmp_path):
    executable = ROOT / ".tools" / "runtime" / "Library" / "bin" / "osmium.exe"
    env = {**os.environ, "PATH": str(executable.parent) + os.pathsep + os.environ["PATH"]}
    def run(*args):
        return subprocess.run([str(executable), *map(str, args)], check=True,
                              capture_output=True, env=env).stdout
    first, second, merged = [tmp_path / name for name in ("first.pbf", "second.pbf", "merged.pbf")]
    run("extract", ROOT / "tests" / "fixtures" / "border.osm", "-b", "-0.02,0,0.06,0.1",
        "-s", "smart", "-o", first)
    run("extract", ROOT / "tests" / "fixtures" / "border.osm", "-b", "0.04,0,0.12,0.1",
        "-s", "smart", "-o", second)
    run("merge", first, second, "-o", merged)
    info = json.loads(run("fileinfo", "-e", "--json", merged))
    assert info["data"]["count"]["ways"] == 2
    assert info["data"]["objects_ordered"]
    assert not info["data"]["multiple_versions"]
    run("check-refs", merged)


@pytest.mark.skipif(not (ROOT / ".tools" / "planetiler.jar").exists(),
                    reason="Native tools unavailable")
def test_native_antimeridian_emits_only_edge_tiles(tmp_path):
    bin_dir = ROOT / ".tools" / "runtime" / "Library" / "bin"
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}
    elements = []
    for offset, west in ((0, 179.6), (10, -179.9)):
        for node, (lon, lat) in enumerate(
            [(west, 10.1), (west + .2, 10.1), (west + .2, 10.3), (west, 10.3)], start=offset + 1
        ):
            elements.append(f'<node id="{node}" version="1" lon="{lon}" lat="{lat}"/>')
    for offset in (0, 10):
        refs = "".join(f'<nd ref="{node}"/>' for node in
                       [offset + 1, offset + 2, offset + 3, offset + 4, offset + 1])
        elements.append(f'<way id="{offset + 1}" version="1">{refs}'
                        '<tag k="landuse" v="forest"/></way>')
    source = tmp_path / "antimeridian.osm"
    source.write_text('<osm version="0.6">' + "".join(elements) + "</osm>")
    pbf = tmp_path / "antimeridian.osm.pbf"
    subprocess.run([str(bin_dir / "osmium.exe"), "cat", str(source), "-o", str(pbf)],
                   check=True, capture_output=True, env=env)
    region = normalize_region({"type": "Polygon", "coordinates": [
        [[179.5, 10], [-179.5, 10], [-179.5, 10.5], [179.5, 10.5], [179.5, 10]]]})
    aoi = tmp_path / "region.wkt"
    aoi.write_text(region.wkt)
    output = tmp_path / "dateline.pmtiles"
    subprocess.run([str(bin_dir / "java.exe"), "-Xmx1g", "-cp", str(ROOT / ".tools" / "planetiler.jar"),
                    str(ROOT / "tiles" / "RegionProfile.java"), f"--osm-path={pbf}",
                    f"--aoi={aoi}", f"--output={output}", "--auxiliary=false",
                    "--only-layers=landcover", "--minzoom=6", "--maxzoom=8", "--threads=2",
                    f"--tmpdir={tmp_path / 'scratch'}"], check=True, capture_output=True, env=env)
    generated = list(tiles(output))
    assert 1 < len(generated) < 20
    assert all(x in {0, (1 << z) - 1} for (z, x, _), _ in generated)
