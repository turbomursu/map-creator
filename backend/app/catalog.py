import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
import psutil
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from app.geometry import describe_region, normalize_region
from app.models import JobSpec

CATALOG_URL = "https://download.geofabrik.de/index-v1.json"
GIB = 1024 ** 3
AUXILIARY = [
    ("lake_centerline.shp.zip", "https://dev.maptiler.download/geodata/omt/lake_centerline.shp.zip"),
    ("water-polygons-split-3857.zip",
     "https://osmdata.openstreetmap.de/download/water-polygons-split-3857.zip"),
    ("natural_earth_vector.sqlite.zip",
     "https://dev.maptiler.download/geodata/omt/natural_earth_vector.sqlite.zip"),
]


def cache_key(source: dict) -> str:
    fields = ("local_path", "size", "mtime_ns") if "local_path" in source else (
        "url", "resolved_url", "size", "etag", "modified")
    identity = {key: source.get(key) for key in fields}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def with_cache_info(root: Path, source: dict) -> dict:
    directory = root / "cache" / cache_key(source)
    filename = "source.pbf" if source["url"].endswith(".pbf") else "source.zip"
    return {**source, "cached": (directory / filename).is_file()
            and (directory / "manifest.json").is_file()}


def trusted_url(url: str):
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname not in
        {"download.geofabrik.de", "dev.maptiler.download", "osmdata.openstreetmap.de"}
        or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("Untrusted data source URL.")
    return url


@contextmanager
def source_stream(client: httpx.Client, method: str, url: str, **kwargs):
    seen = set()
    for _ in range(6):
        trusted_url(url)
        if url in seen:
            raise ValueError("Data source redirect loop.")
        seen.add(url)
        with client.stream(method, url, follow_redirects=False, **kwargs) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("Data source redirect is missing its Location header.")
                url = str(response.url.join(location))
                continue
            response.raise_for_status()
            yield response
            return
    raise ValueError("Too many data source redirects.")


def source_headers(client: httpx.Client, url: str) -> dict:
    with source_stream(client, "HEAD", url, headers={"Accept-Encoding": "identity"}) as response:
        size = int(response.headers.get("content-length", 0))
        if size <= 0:
            raise ValueError(f"Source did not advertise its download size: {url}")
        return {"size": size, "etag": response.headers.get("etag"),
                "modified": response.headers.get("last-modified"),
                "resolved_url": str(response.url)}


class Catalog:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "catalog.json"
        self.lock = threading.Lock()

    def load(self, refresh=False):
        with self.lock:
            return self._load(refresh)

    def _load(self, refresh=False):
        if refresh or not self.path.exists() or time.time() - self.path.stat().st_mtime > 86400:
            with (httpx.Client(timeout=60, follow_redirects=False) as client,
                  source_stream(client, "GET", CATALOG_URL) as response):
                response.read()
                content = response.json()
            if content.get("type") != "FeatureCollection":
                raise ValueError("Unexpected provider catalog format.")
            temp = self.path.with_suffix(".part")
            temp.write_text(json.dumps(content), encoding="utf-8")
            temp.replace(self.path)
        return json.loads(self.path.read_text(encoding="utf-8"))

    def resolve(self, region, ids: list[str], catalog: dict):
        target = region
        candidates = []
        for feature in catalog["features"]:
            props = feature["properties"]
            if not props.get("urls", {}).get("pbf"):
                continue
            footprint = shape(feature["geometry"])
            if footprint.is_valid and footprint.intersects(target):
                candidates.append((props, footprint))
        if ids:
            chosen = [(p, g) for p, g in candidates if p["id"] in ids]
            if {p["id"] for p, _ in chosen} != set(ids):
                raise ValueError("One or more selected sources do not cover the region.")
        else:
            parents = [(p, g) for p, g in candidates if g.covers(target)]
            if parents:
                chosen = [min(parents, key=lambda item: (item[1].area, item[0]["id"]))]
            else:
                chosen = []
                uncovered = target
                while uncovered.area > 1e-10:
                    options = [(p, g) for p, g in candidates if (p, g) not in chosen]
                    if not options or len(chosen) >= 32:
                        break
                    best = max(options, key=lambda item: (
                        item[1].intersection(uncovered).area / max(item[1].area, 1e-12),
                        item[1].intersection(uncovered).area, item[0]["id"]))
                    if best[1].intersection(uncovered).area <= 1e-12:
                        break
                    chosen.append(best)
                    uncovered = uncovered.difference(best[1])
        if not chosen or not unary_union([g for _, g in chosen]).buffer(1e-7).covers(target):
            raise ValueError("Provider extracts do not fully cover this region and its boundary buffer.")
        return [{"id": p["id"], "name": p["name"], "url": trusted_url(p["urls"]["pbf"]),
                 "coverage": mapping(g)} for p, g in sorted(chosen, key=lambda item: item[0]["id"])]

    def preflight(self, spec: JobSpec):
        region = normalize_region(spec.geometry)
        sources = self.resolve(region, spec.sources, self.load())
        auxiliary = []
        with httpx.Client(timeout=60, follow_redirects=False) as client:
            for source in sources:
                source.update(source_headers(client, source["url"]))
            for filename, url in AUXILIARY:
                auxiliary.append({"id": filename, "url": url, **source_headers(client, url)})
        sources = [with_cache_info(self.root, source) for source in sources]
        auxiliary = [with_cache_info(self.root, source) for source in auxiliary]
        import shutil
        total = sum(s["size"] for s in sources)
        download = total + sum(s["size"] for s in auxiliary)
        disk_required = download + total * 14 + 2 * GIB
        free = shutil.disk_usage(self.root).free
        available = psutil.virtual_memory().available
        errors = []
        if free < disk_required:
            errors.append(f"Need approximately {disk_required / GIB:.1f} GiB free disk; {free / GIB:.1f} available.")
        if spec.memory_gb * GIB + GIB > available:
            errors.append("JVM memory budget must leave at least 1 GiB of currently available RAM.")
        if total * 0.5 > spec.memory_gb * GIB:
            errors.append("Selected input size requires a larger JVM memory budget (initial 0.5x heuristic).")
        return {**describe_region(region), "sources": sources, "auxiliary": auxiliary,
                "download_bytes": sum(s["size"] for s in sources + auxiliary if not s["cached"]),
                "disk_required": disk_required,
                "free_disk": free, "available_memory": available, "errors": errors,
                "warnings": [
                    "Resource estimates are conservative heuristics, not a country-scale benchmark.",
                    "The engine reads complete covering extracts to preserve crossing ways and enclosing polygons.",
                    "Multiple extracts must have identical replication snapshots; unsafe merges will fail.",
                    "Provider files can contain more than the selected country or region.",
                ]}
import hashlib
