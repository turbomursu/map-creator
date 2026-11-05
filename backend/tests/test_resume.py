import app.pipeline
import httpx
import pytest
from app.catalog import cache_key, source_headers
from app.pipeline import Pipeline
from app.store import Store


@pytest.mark.parametrize("offset", [4, 12])
def test_safe_partial_resume_and_complete_partial_restart(tmp_path, monkeypatch, offset):
    data = b"fixture-data"
    source = {"id": "resume", "url": "https://download.geofabrik.de/test.osm.pbf",
              "size": len(data), "etag": '"stable"', "modified": None}
    store = Store(tmp_path)
    job = store.create({"name": "resume"}, {})
    directory = tmp_path / "cache" / cache_key(source)
    directory.mkdir(parents=True)
    (directory / "download.part").write_bytes(data[:offset])

    def handler(request):
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(data)), "etag": '"stable"'})
        if offset < len(data):
            assert request.headers["range"] == f"bytes={offset}-"
            assert request.headers["if-range"] == '"stable"'
            return httpx.Response(206, content=data[offset:], headers={
                "etag": '"stable"', "content-range": f"bytes {offset}-{len(data) - 1}/{len(data)}"})
        assert "range" not in request.headers
        return httpx.Response(200, content=data, headers={"etag": '"stable"'})

    original = httpx.Client
    monkeypatch.setattr(app.pipeline.httpx, "Client", lambda **kwargs:
                        original(transport=httpx.MockTransport(handler), **kwargs))
    path = Pipeline(store, tmp_path).acquire(job["id"], source)
    assert path.read_bytes() == data


def test_preflight_pins_dated_snapshot_and_resumes_without_latest(tmp_path, monkeypatch):
    data = b"fixture-data"
    latest = "https://download.geofabrik.de/europe/finland-latest.osm.pbf"
    dated = latest.replace("latest", "261103")
    requests = []

    def handler(request):
        requests.append(request)
        if str(request.url) == latest:
            return httpx.Response(307, headers={"location": "finland-261103.osm.pbf"})
        assert str(request.url) == dated
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(data)), "etag": '"stable"'})
        assert request.headers["range"] == "bytes=4-"
        assert request.headers["if-range"] == '"stable"'
        return httpx.Response(206, content=data[4:], headers={
            "etag": '"stable"', "content-range": f"bytes 4-{len(data) - 1}/{len(data)}"})

    original = httpx.Client
    transport = httpx.MockTransport(handler)
    with original(transport=transport) as client:
        source = {"id": "finland", "url": latest, **source_headers(client, latest)}
    requests.clear()
    store = Store(tmp_path)
    job = store.create({"name": "dated"}, {})
    directory = tmp_path / "cache" / cache_key(source)
    directory.mkdir(parents=True)
    (directory / "download.part").write_bytes(data[:4])
    monkeypatch.setattr(app.pipeline.httpx, "Client", lambda **kwargs:
                        original(transport=transport, **kwargs))
    assert Pipeline(store, tmp_path).acquire(job["id"], source).read_bytes() == data
    assert all(str(request.url) == dated for request in requests)


@pytest.mark.parametrize("redirect_method", ["HEAD", "GET"])
def test_download_rejects_retargeted_snapshot_even_with_same_validators(
    tmp_path, monkeypatch, redirect_method
):
    dated = "https://download.geofabrik.de/finland-261103.osm.pbf"
    source = {"id": "finland", "url": dated, "resolved_url": dated,
              "size": 12, "etag": '"stable"', "modified": None}

    def handler(request):
        if str(request.url) == dated and request.method == redirect_method:
            return httpx.Response(307, headers={"location": "/finland-261104.osm.pbf"})
        return httpx.Response(200, content=b"fixture-data",
                              headers={"content-length": "12", "etag": '"stable"'})

    original = httpx.Client
    monkeypatch.setattr(app.pipeline.httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))
    store = Store(tmp_path)
    job = store.create({"name": "changed"}, {})
    with pytest.raises(ValueError, match="changed"):
        Pipeline(store, tmp_path).acquire(job["id"], source)
    assert not (tmp_path / "cache" / cache_key(source) / "source.pbf").exists()


def test_download_redirect_never_requests_untrusted_target(tmp_path, monkeypatch):
    source = {"id": "unsafe", "url": "https://download.geofabrik.de/test.osm.pbf",
              "size": 12, "etag": '"stable"', "modified": None}
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": "12", "etag": '"stable"'})
        return httpx.Response(307, headers={"location": "https://evil.test/snapshot"})

    original = httpx.Client
    monkeypatch.setattr(app.pipeline.httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))
    store = Store(tmp_path)
    job = store.create({"name": "unsafe"}, {})
    with pytest.raises(ValueError, match="Untrusted"):
        Pipeline(store, tmp_path).acquire(job["id"], source)
    assert len(requests) == 2
    assert all(request.url.host == "download.geofabrik.de" for request in requests)
