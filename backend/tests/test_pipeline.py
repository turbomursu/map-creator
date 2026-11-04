import json
from pathlib import Path

import httpx
import pytest
from app.models import JobSpec
from app.pipeline import Pipeline
from app.store import Store

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / ".tools" / "native fixture space" / "input.osm.pbf"


def local_source():
    stat = SOURCE.stat()
    return {"id": "fixture", "name": "fixture", "local_path": str(SOURCE),
            "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


@pytest.mark.skipif(not SOURCE.exists(), reason="Run native fixtures first")
@pytest.mark.parametrize("extension", ["pmtiles", "mbtiles"])
def test_real_pipeline_publishes_only_validated_output(tmp_path, extension):
    tmp_path = tmp_path / "Map data \u00e4\u5730"
    tmp_path.mkdir()
    store = Store(tmp_path)
    geometry = json.loads((ROOT / "tests" / "fixtures" / "region.geojson").read_text())
    spec = JobSpec(geometry=geometry, minzoom=12, maxzoom=14,
                   layers=["landcover", "transportation"], format=extension, memory_gb=1)
    job = store.create(spec.model_dump(), {"sources": [local_source()], "auxiliary": [],
                                         "disk_required": 0})
    Pipeline(store, ROOT / ".tools").execute(store.claim())
    completed = store.get(job["id"])
    assert completed["state"] == "ready", completed["error"]
    directory = store.directory(job["id"])
    assert (directory / f"map.{extension}").is_file()
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["sources"][0]["sha256"]
    assert manifest["artifact"]["metadata"]["attribution"]
    assert not (directory / "scratch").exists()


@pytest.mark.skipif(not SOURCE.exists(), reason="Run native fixtures first")
def test_pipeline_rejects_unknown_merge_snapshots(tmp_path):
    store = Store(tmp_path)
    geometry = json.loads((ROOT / "tests" / "fixtures" / "region.geojson").read_text())
    job = store.create(JobSpec(geometry=geometry).model_dump(),
                       {"sources": [local_source(), local_source()], "auxiliary": [],
                        "disk_required": 0})
    Pipeline(store, ROOT / ".tools").execute(store.claim())
    failed = store.get(job["id"])
    assert failed["state"] == "failed"
    assert "replication" in failed["error"]
    assert not (store.directory(job["id"]) / "map.pmtiles").exists()


def test_cancelled_job_never_publishes(tmp_path):
    store = Store(tmp_path)
    job = store.create({"name": "cancel", "format": "pmtiles"}, {})
    store.cancel(job["id"])
    Pipeline(store, ROOT / ".tools").execute(store.claim())
    assert store.get(job["id"])["state"] == "cancelled"
    assert not (store.directory(job["id"]) / "map.pmtiles").exists()


def test_download_resume_requires_matching_source_validators(tmp_path, monkeypatch):
    import app.pipeline
    store = Store(tmp_path)
    job = store.create({"name": "download"}, {})
    data = b"fixture-data"
    source = {"id": "fixture", "url": "https://download.geofabrik.de/test.osm.pbf",
              "size": len(data), "etag": '"stable"', "modified": None}
    pipeline = Pipeline(store, ROOT / ".tools")
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(data)), "etag": '"stable"'})
        return httpx.Response(200, content=data, headers={"etag": '"stable"'})

    original = httpx.Client
    monkeypatch.setattr(app.pipeline.httpx, "Client", lambda **kwargs:
                        original(transport=httpx.MockTransport(handler), **kwargs))
    path = pipeline.acquire(job["id"], source)
    assert path.read_bytes() == data
    assert pipeline.acquire(job["id"], source) == path
    assert len(requests) == 2  # A validated immutable cache does not contact the server again.
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        pipeline.acquire(job["id"], source)


def test_download_rejects_changed_preflight_snapshot(tmp_path, monkeypatch):
    import app.pipeline
    store = Store(tmp_path)
    job = store.create({"name": "changed"}, {})
    original = httpx.Client
    monkeypatch.setattr(app.pipeline.httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"content-length": "10", "etag": '"new"'})), **kwargs))
    with pytest.raises(ValueError, match="changed since preflight"):
        Pipeline(store, ROOT / ".tools").acquire(job["id"], {
            "id": "changed", "url": "https://download.geofabrik.de/test.osm.pbf",
            "size": 10, "etag": '"old"', "modified": None})
