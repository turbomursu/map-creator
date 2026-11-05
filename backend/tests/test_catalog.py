import httpx
import pytest
from app.catalog import Catalog, cache_key, source_headers, source_stream, trusted_url
from app.geometry import normalize_region
from shapely.geometry import box, mapping


def feature(name, region):
    return {"type": "Feature", "geometry": mapping(region), "properties": {
        "id": name, "name": name, "urls": {"pbf": f"https://download.geofabrik.de/{name}.osm.pbf"}}}


def test_resolves_smallest_covering_parent_deterministically(tmp_path):
    catalog = {"features": [feature("continent", box(-10, -10, 10, 10)),
                            feature("country", box(0, 0, 2, 2)),
                            feature("town", box(0, 0, .5, .5))]}
    selected = Catalog(tmp_path).resolve(normalize_region(mapping(box(.6, .6, 1, 1))), [], catalog)
    assert [source["id"] for source in selected] == ["country"]


def test_resolves_cross_border_sources_and_rejects_coverage_gaps(tmp_path):
    catalog = {"features": [feature("west", box(0, 0, 1.1, 1)),
                            feature("east", box(.9, 0, 2, 1))]}
    region = normalize_region(mapping(box(.5, .2, 1.5, .8)))
    selected = Catalog(tmp_path).resolve(region, ["west", "east"], catalog)
    assert {source["id"] for source in selected} == {"west", "east"}
    catalog["features"][1] = feature("east", box(1.2, 0, 2, 1))
    with pytest.raises(ValueError, match="fully cover"):
        Catalog(tmp_path).resolve(region, [], catalog)


@pytest.mark.parametrize("url", [
    "http://download.geofabrik.de/planet.pbf", "https://example.com/data",
    "https://download.geofabrik.de.evil.test/x", "https://user@download.geofabrik.de/x",
    "https://download.geofabrik.de:444/x",
])
def test_restricts_source_hosts(url):
    with pytest.raises(ValueError):
        trusted_url(url)


def test_requires_source_size_and_does_not_follow_untrusted_redirects():
    transport = httpx.MockTransport(lambda request: httpx.Response(
        302, headers={"location": "https://evil.test/x"}))
    with httpx.Client(transport=transport) as client, pytest.raises(ValueError, match="Untrusted"):
        source_headers(client, "https://download.geofabrik.de/data.pbf")


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_source_headers_resolves_relative_dated_redirect(status):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("latest.osm.pbf"):
            return httpx.Response(status, headers={"location": "finland-261103.osm.pbf"})
        return httpx.Response(200, headers={"content-length": "123", "etag": '"dated"'})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result = source_headers(client, "https://download.geofabrik.de/europe/finland-latest.osm.pbf")
    assert result["resolved_url"] == "https://download.geofabrik.de/europe/finland-261103.osm.pbf"
    assert result["size"] == 123
    assert [request.method for request in requests] == ["HEAD", "HEAD"]


@pytest.mark.parametrize("location", [
    "https://evil.test/file", "http://download.geofabrik.de/file",
    "https://user@download.geofabrik.de/file", "https://download.geofabrik.de:444/file",
])
def test_redirect_target_is_checked_before_request(location):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": location})

    with (httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client,
          pytest.raises(ValueError, match="Untrusted")):
        source_headers(client, "https://download.geofabrik.de/latest.osm.pbf")
    assert len(requests) == 1


@pytest.mark.parametrize("mode, message", [
    ("loop", "loop"), ("chain", "Too many"), ("missing", "Location"),
])
def test_redirect_failures_are_bounded(mode, message):
    requests = []

    def handler(request):
        requests.append(request)
        headers = {} if mode == "missing" else {
            "location": "/latest" if mode == "loop" else f"/snapshot-{len(requests)}"}
        return httpx.Response(307, headers=headers)

    with (httpx.Client(transport=httpx.MockTransport(handler)) as client,
          pytest.raises(ValueError, match=message)):
        source_headers(client, "https://download.geofabrik.de/latest")
    assert len(requests) <= 6


def test_cache_identity_includes_reviewed_snapshot_url():
    source = {"url": "https://download.geofabrik.de/finland-latest.osm.pbf",
              "resolved_url": "https://download.geofabrik.de/finland-261103.osm.pbf",
              "size": 123, "etag": '"same"', "modified": None}
    assert cache_key(source) != cache_key({
        **source, "resolved_url": "https://download.geofabrik.de/finland-261104.osm.pbf"})


@pytest.mark.parametrize("error_type", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError])
def test_network_failure_identifies_redirect_target_and_operation(error_type):
    original = "https://download.geofabrik.de/latest.osm.pbf"
    target = "https://download.geofabrik.de/dated.osm.pbf"

    def handler(request):
        if str(request.url) == original:
            return httpx.Response(307, headers={"location": target})
        raise error_type("[WinError 10060]", request=request)

    with (httpx.Client(transport=httpx.MockTransport(handler)) as client,
          pytest.raises(RuntimeError) as caught):
        source_headers(client, original)
    message = str(caught.value)
    assert target in message
    assert "metadata check (HEAD)" in message
    assert "download.geofabrik.de" in message
    assert "firewall, VPN and proxy" in message
    assert "[WinError 10060]" in message
    assert isinstance(caught.value.__cause__, error_type)


def test_catalog_get_failure_has_source_diagnostics(tmp_path, monkeypatch):
    import app.catalog

    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    original = httpx.Client
    monkeypatch.setattr(app.catalog.httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(RuntimeError, match=r"data transfer \(GET\).*index-v1.json"):
        Catalog(tmp_path).load()
    assert not (tmp_path / "catalog.json").exists()


def test_http_failure_preserves_status_and_source():
    url = "https://osmdata.openstreetmap.de/download/water-polygons-split-3857.zip"
    with (httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(503))) as client,
          pytest.raises(RuntimeError, match="HTTP 503") as caught):
        source_headers(client, url)
    assert url in str(caught.value)


def test_transfer_read_timeout_is_contextualized_and_not_silently_truncated():
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            raise httpx.ReadTimeout("interrupted", request=httpx.Request(
                "GET", "https://download.geofabrik.de/dated.osm.pbf"))
            yield b""  # pragma: no cover

    with (httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=BrokenStream()))) as client,
          pytest.raises(RuntimeError, match=r"data transfer \(GET\).*dated.osm.pbf"),
          source_stream(client, "GET", "https://download.geofabrik.de/dated.osm.pbf") as response):
        response.read()
