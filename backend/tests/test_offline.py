import json
import socket
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from app.main import create_app
from app.models import JobSpec
from app.pipeline import Pipeline
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / ".tools" / "native fixture space" / "input.osm.pbf"


@pytest.mark.skipif(not SOURCE.exists(), reason="Generate native fixtures first")
@pytest.mark.parametrize("extension", ["pmtiles", "mbtiles"])
def test_local_selection_to_export_requires_no_external_network(tmp_path, monkeypatch, extension):
    import tkinter
    from tkinter import filedialog

    def blocked(*args, **kwargs):
        raise AssertionError("External networking is blocked in the local workflow")

    connect = socket.socket.connect

    def local_connect(sock, address):
        if address[0] not in {"127.0.0.1", "::1"}:
            blocked()
        return connect(sock, address)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(socket.socket, "connect", local_connect)
    monkeypatch.setenv("JAVA_TOOL_OPTIONS",
                       "-Dhttp.proxyHost=127.0.0.1 -Dhttp.proxyPort=9 "
                       "-Dhttps.proxyHost=127.0.0.1 -Dhttps.proxyPort=9")
    monkeypatch.setattr(tkinter, "Tk", Mock())
    monkeypatch.setattr(filedialog, "askopenfilenames", lambda **kwargs: [str(SOURCE)])
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        client.headers["x-map-token"] = client.get("/api/session").json()["token"]
        assert client.get("/").status_code == 200
        assert client.get("/world.geojson").status_code == 200
        imported = client.post("/api/import")
        assert imported.status_code == 200, imported.text
        geometry = json.loads((ROOT / "tests" / "fixtures" / "region.geojson").read_text())
        spec = JobSpec(geometry=geometry, sources=[imported.json()[0]["id"]],
                       minzoom=12, maxzoom=14, memory_gb=1,
                       layers=["landcover", "transportation"], format=extension).model_dump()
        review = client.post("/api/preflight", json=spec)
        assert review.status_code == 200, review.text
        plan = review.json()
        assert plan["auxiliary"] == []
        assert plan["download_bytes"] == 0
        assert any("Local OSM only" in warning for warning in plan["warnings"])
        created = client.post("/api/jobs", json={"spec": spec, "preflight_id": plan["preflight_id"]})
        assert created.status_code == 201, created.text
        job_id = created.json()["id"]
        Pipeline(app.state.store, ROOT / ".tools").execute(app.state.store.claim())
        result = app.state.store.get(job_id)
        assert result["state"] == "ready", result["error"]
        manifest = client.get(f"/api/jobs/{job_id}/manifest").json()
        assert manifest["auxiliary"] == []
        assert manifest["sources"][0]["sha256"]
        artifact = client.get(f"/api/jobs/{job_id}/artifact")
        assert artifact.status_code == 200 and artifact.content
        assert client.get(f"/api/jobs/{job_id}/artifact",
                          headers={"Range": "bytes=0-6"}).status_code == 206
        if extension == "mbtiles":
            import sqlite3
            from contextlib import closing

            with closing(sqlite3.connect(app.state.store.directory(job_id) / "map.mbtiles")) as db:
                z, x, row = db.execute("SELECT zoom_level,tile_column,tile_row FROM tiles LIMIT 1").fetchone()
            assert client.get(f"/api/jobs/{job_id}/tiles/{z}/{x}/{(1 << z) - 1 - row}.pbf").status_code == 200


def test_legacy_remote_job_is_rejected_before_network(tmp_path, monkeypatch):
    from app.store import Store

    def blocked(*args, **kwargs):
        raise AssertionError("Remote acquisition must not run")

    monkeypatch.setattr(Pipeline, "acquire", blocked)
    store = Store(tmp_path)
    job = store.create({"name": "Legacy provider job", "format": "pmtiles"}, {
        "sources": [{"url": "https://download.geofabrik.de/old.osm.pbf"}], "auxiliary": []})
    Pipeline(store, ROOT / ".tools").execute(store.claim())
    assert store.get(job["id"])["state"] == "failed"
    assert "local OSM inputs only" in store.get(job["id"])["error"]
