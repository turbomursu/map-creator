import json
import math

from pyproj import Geod
from shapely import affinity, orient_polygons
from shapely.geometry import LineString, MultiPolygon, Polygon, mapping, shape
from shapely.ops import split
from shapely.validation import explain_validity

MERCATOR_LIMIT = 85.05112878


def normalize_region(value: dict) -> MultiPolygon:
    try:
        return _normalize_region(value)
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError("Malformed region coordinates. Import a valid GeoJSON polygon.") from error


def _normalize_region(value: dict) -> MultiPolygon:
    if not isinstance(value, dict):
        raise TypeError("Region must be a GeoJSON geometry object.")
    if value.get("type") == "Feature":
        value = value.get("geometry") or {}
    if not isinstance(value, dict):
        raise TypeError("GeoJSON Feature must contain a geometry object.")
    if value.get("type") not in {"Polygon", "MultiPolygon"}:
        raise ValueError("Select or import a Polygon or MultiPolygon.")
    if len(json.dumps(value)) > 1_000_000:
        raise ValueError("Region geometry is too complex (maximum 1 MB).")
    polygons = value["coordinates"] if value["type"] == "MultiPolygon" else [value["coordinates"]]
    normalized = []
    vertex_count = 0
    for rings in polygons:
        if not rings:
            raise ValueError("Region polygon has no exterior ring.")
        unwrapped = []
        for ring in rings:
            vertex_count += len(ring)
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("Every region ring must be closed and have at least four positions.")
            points = []
            for position in ring:
                if len(position) != 2:
                    raise ValueError("Use two-dimensional longitude/latitude coordinates.")
                lon, lat = position
                if not all(type(n) in (int, float) and math.isfinite(n) for n in position):
                    raise ValueError("Region coordinates must be finite numbers.")
                if not -180 <= lon <= 180 or abs(lat) > MERCATOR_LIMIT:
                    raise ValueError("Coordinates must fit longitude +/-180 and Web Mercator latitude.")
                if points:
                    lon += 360 * round((points[-1][0] - lon) / 360)
                points.append((lon, lat))
            if unwrapped:
                shift = 360 * round((unwrapped[0][0][0] - points[0][0]) / 360)
                points = [(x + shift, y) for x, y in points]
            unwrapped.append(points)
        polygon = Polygon(unwrapped[0], unwrapped[1:])
        if polygon.is_empty or not polygon.is_valid or polygon.area == 0:
            raise ValueError(f"Invalid region: {explain_validity(polygon)}")
        if polygon.bounds[2] - polygon.bounds[0] > 360:
            raise ValueError("A region cannot wrap around the world.")
        pieces = [polygon]
        for meridian in (-180, 180, 540):
            pieces = [
                part
                for piece in pieces
                for part in split(piece, LineString([(meridian, -90), (meridian, 90)])).geoms
            ]
        for piece in pieces:
            shift = 360 * math.floor((piece.representative_point().x + 180) / 360)
            normalized.append(affinity.translate(piece, xoff=-shift))
    if vertex_count > 20_000:
        raise ValueError("Region has more than 20,000 vertices.")
    result = MultiPolygon(normalized)
    if result.is_empty or not result.is_valid:
        raise ValueError(f"Invalid combined region: {explain_validity(result)}")
    return orient_polygons(result, exterior_cw=False)


def describe_region(region: MultiPolygon) -> dict:
    geod = Geod(ellps="WGS84")
    area = sum(abs(geod.geometry_area_perimeter(p)[0]) for p in region.geoms)
    return {"geometry": mapping(region), "bounds": region.bounds, "area_km2": area / 1e6}


def processing_region(region: MultiPolygon):
    # PBF extraction retains full ways; this extra margin protects polygon-boundary vertices.
    return region.buffer(0.01).intersection(shape({
        "type": "Polygon",
        "coordinates": [[[-180, -MERCATOR_LIMIT], [180, -MERCATOR_LIMIT],
                         [180, MERCATOR_LIMIT], [-180, MERCATOR_LIMIT], [-180, -MERCATOR_LIMIT]]],
    }))
