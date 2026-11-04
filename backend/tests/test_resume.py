import app.pipeline
import httpx
import pytest
from app.catalog import cache_key
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
