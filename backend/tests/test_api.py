import json
import shutil
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest
from app.main import create_app
from app.models import JobSpec
from fastapi.testclient import TestClient
from shapely.geometry import box, mapping, shape

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def api(tmp_path):
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        token = client.get("/api/session").json()["token"]
        client.headers["x-map-token"] = token
        yield client, app


def test_loopback_token_and_origin_guards(api):
    client, _ = api
    assert client.post("/api/jobs", headers={"x-map-token": ""}, json={}).status_code == 403
    assert client.get("/api/session", headers={"host": "evil.test"}).status_code == 403
    assert client.get("/api/session", headers={"origin": "https://evil.test"}).status_code == 403
    assert client.get("/api/session", headers={"sec-fetch-site": "cross-site"}).status_code == 403


def test_preflight_requires_local_inputs_without_catalog_loading(api, monkeypatch):
    client, app = api

    def fail(*args):
        raise AssertionError("Catalog must not be loaded")

    monkeypatch.setattr(app.state.catalog, "load", fail)
    response = client.post("/api/preflight", json=JobSpec(
        geometry=mapping(box(0, 0, 1, 1))).model_dump())
    assert response.status_code == 422
    assert "Choose local" in response.json()["detail"]
    assert client.get("/api/catalog").status_code == 410
    assert not app.state.preflights


def test_preflight_is_bound_to_configuration_and_single_use(api, monkeypatch):
    client, app = api
    app.state.imports["local-test"] = {"id": "local-test", "name": "local fixture",
                                     "size": 1, "coverage": mapping(box(-1, -1, 2, 2))}
    spec = JobSpec(geometry=mapping(box(0, 0, 1, 1)), sources=["local-test"],
                   memory_gb=1).model_dump()
    review = client.post("/api/preflight", json=spec).json()
    changed = {**spec, "maxzoom": 10}
    assert client.post("/api/jobs", json={"spec": changed,
                                        "preflight_id": review["preflight_id"]}).status_code == 409
    created = client.post("/api/jobs", json={"spec": spec,
                                          "preflight_id": review["preflight_id"]})
    assert created.status_code == 201
    assert client.post("/api/jobs", json={"spec": spec,
                                        "preflight_id": review["preflight_id"]}).status_code == 409
    job = created.json()
    assert job["state"] == "queued"
    assert client.delete(f"/api/jobs/{job['id']}/scratch").status_code == 409
    assert client.delete("/api/cache").status_code == 409
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200


def test_store_claim_recovery_and_bounded_events(api):
    _, app = api
    store = app.state.store
    spec = JobSpec(geometry=mapping(box(0, 0, 1, 1))).model_dump()
    job = store.create(spec, {})
    assert store.claim()["id"] == job["id"]
    assert store.claim() is None
    store.recover()
    assert store.get(job["id"])["state"] == "interrupted"
    assert store.get(job["id"])["error"]
    assert store.events(job["id"]) == []


@pytest.mark.skipif(not (ROOT / ".tools" / "native fixture space" / "input.osm.pbf").exists(),
                    reason="Generate native fixtures first")
def test_native_import_handles_unicode_filenames(api, tmp_path, monkeypatch):
    import tkinter
    from tkinter import filedialog

    client, app = api
    source = tmp_path / "source \u00e4\u5730.osm.pbf"
    shutil.copyfile(ROOT / ".tools" / "native fixture space" / "input.osm.pbf", source)
    monkeypatch.setattr(tkinter, "Tk", Mock())
    monkeypatch.setattr(filedialog, "askopenfilenames", lambda **kwargs: [str(source)])
    response = client.post("/api/import")
    assert response.status_code == 200, response.text
    imported = app.state.imports[response.json()[0]["id"]]
    assert imported["local_path"] == str(source)
    assert imported["coverage"]["type"] == "Polygon"


def test_import_selection_unions_overlapping_bounds(api, tmp_path, monkeypatch):
    import tkinter
    from tkinter import filedialog

    client, _ = api
    paths = [tmp_path / f"part{index}.osm.pbf" for index in range(2)]
    for path in paths:
        path.write_bytes(b"header-fixture")
    headers = iter([
        {"header": {"with_history": False, "boxes": [[0, 0, 2, 1]]}},
        {"header": {"with_history": False, "boxes": [[1, 0, 3, 1]]}},
    ])
    monkeypatch.setattr(tkinter, "Tk", Mock())
    monkeypatch.setattr(filedialog, "askopenfilenames", lambda **kwargs: [str(path) for path in paths])
    monkeypatch.setattr("app.main.run_native", lambda *args, **kwargs: json.dumps(next(headers)))
    response = client.post("/api/import")
    assert response.status_code == 200, response.text
    selection = shape(response.json()[0]["selection"])
    assert selection.is_valid
    assert selection.equals(box(0, 0, 3, 1))


@pytest.mark.skipif(not (ROOT / ".tools" / "native fixture space" / "output.pmtiles").exists(),
                    reason="Generate native fixtures first")
def test_artifact_ranges_and_mbtiles_xyz_tms(api):
    client, app = api
    store = app.state.store
    for extension in ("pmtiles", "mbtiles"):
        spec = JobSpec(geometry=mapping(box(0, 0, 1, 1)), format=extension).model_dump()
        job = store.create(spec, {})
        directory = store.directory(job["id"])
        shutil.copyfile(ROOT / ".tools" / "native fixture space" / f"output.{extension}",
                        directory / f"map.{extension}")
        (directory / "manifest.json").write_text(json.dumps({"name": "fixture"}))
        store.update(job["id"], "ready")
        endpoint = f"/api/jobs/{job['id']}"
        response = client.get(f"{endpoint}/artifact", headers={"Range": "bytes=0-6"})
        assert response.status_code == 206
        assert len(response.content) == 7
        assert response.headers["content-range"].startswith("bytes 0-6/")
        assert client.get(f"{endpoint}/artifact", headers={"Range": "bytes=999999999-"}).status_code == 416
        assert client.head(f"{endpoint}/artifact").status_code == 200
        if extension == "mbtiles":
            with sqlite3.connect(directory / "map.mbtiles") as db:
                z, x, tms = db.execute("SELECT zoom_level,tile_column,tile_row FROM tiles LIMIT 1").fetchone()
            result = client.get(f"{endpoint}/tiles/{z}/{x}/{(1 << z) - 1 - tms}.pbf")
            assert result.status_code == 200
            assert len(result.content) > 0
            assert client.get(f"{endpoint}/tiles/2/0/8.pbf").status_code == 404
        assert client.post(f"{endpoint}/cancel").status_code == 409
