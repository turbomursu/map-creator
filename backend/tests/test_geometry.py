import pytest
from app.geometry import describe_region, normalize_region
from app.models import JobSpec
from shapely.geometry import Polygon, box, mapping


def test_normalization_preserves_holes_and_geodesic_area():
    region = normalize_region(mapping(Polygon(
        [(1, 1), (2, 1), (2, 2), (1, 2), (1, 1)],
        [[(1.2, 1.2), (1.2, 1.8), (1.8, 1.8), (1.8, 1.2), (1.2, 1.2)]],
    )))
    assert len(region.geoms[0].interiors) == 1
    assert 7000 < describe_region(region)["area_km2"] < 9000


def test_antimeridian_is_two_small_polygons_not_almost_the_world():
    region = normalize_region({"type": "Polygon", "coordinates": [
        [[179, 10], [-179, 10], [-179, 12], [179, 12], [179, 10]],
    ]})
    assert len(region.geoms) == 2
    assert region.area == pytest.approx(4)
    assert all(polygon.bounds[2] - polygon.bounds[0] == 1 for polygon in region.geoms)


def test_geodesic_area_subtracts_holes_regardless_of_input_ring_orientation():
    exterior = [(1, 1), (2, 1), (2, 2), (1, 2), (1, 1)]
    hole = [(1.2, 1.2), (1.8, 1.2), (1.8, 1.8), (1.2, 1.8), (1.2, 1.2)]
    first = normalize_region(mapping(Polygon(exterior, [hole])))
    second = normalize_region(mapping(Polygon(exterior[::-1], [hole])))
    assert describe_region(first)["area_km2"] == pytest.approx(describe_region(second)["area_km2"])
    assert 7000 < describe_region(first)["area_km2"] < 9000


@pytest.mark.parametrize("value", [
    {"type": "Point", "coordinates": [0, 0]},
    {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 1]]]},
    {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]},
    {"type": "Polygon", "coordinates": [[[0, 86], [1, 86], [1, 87], [0, 86]]]},
    {"type": "Polygon", "coordinates": []},
    {"type": "Polygon"},
    {"type": "Polygon", "coordinates": 9},
    {"type": "Feature", "geometry": None},
    {"type": "Feature", "geometry": [1, 2]},
    {"type": "Polygon", "coordinates": [[[True, 0], [1, 1], [1, 0], [True, 0]]]},
])
def test_invalid_regions_fail_explicitly(value):
    with pytest.raises(ValueError):
        normalize_region(value)


def test_configuration_rejects_unknown_layers_and_reversed_zooms():
    for fields in ({"layers": ["fake"]}, {"minzoom": 10, "maxzoom": 3},
                   {"format": "zip"}, {"layers": ["water", "water"]}):
        with pytest.raises(ValueError):
            JobSpec(geometry=mapping(box(0, 0, 1, 1)), **fields)
