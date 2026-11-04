import httpx
import pytest
from app.catalog import Catalog, source_headers, trusted_url
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
    with httpx.Client(transport=transport) as client, pytest.raises(httpx.HTTPStatusError):
        source_headers(client, "https://download.geofabrik.de/data.pbf")
