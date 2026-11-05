import json
import logging
import os
import shutil
import time
from pathlib import Path

import httpx

from app.artifacts import checksum, validate
from app.catalog import GIB, cache_key, source_headers, source_stream, trusted_url
from app.geometry import normalize_region
from app.process import Cancelled, run_native

LOGGER = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, store, tools: Path):
        self.store = store
        self.tools = tools
        self.bin = tools / "runtime" / "Library" / "bin"
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"]}

    def check(self, job_id):
        if self.store.cancelled(job_id):
            raise Cancelled("Cancelled by user.")
        if shutil.disk_usage(self.store.root).free < 100 * 1024 ** 2:
            raise ValueError("Less than 100 MiB disk space remains. Job stopped to protect the workspace.")

    def cancelled(self, job_id):
        self.check(job_id)
        return False

    def digest(self, job_id, path):
        return checksum(path, lambda: self.check(job_id))

    def run(self, job_id, *args):
        job = self.store.get(job_id)
        directory = self.store.directory(job_id)
        # Osmium's narrow Windows argv breaks UTF-8 metadata for non-ASCII absolute paths.
        relative = Path(args[0]).name == "osmium.exe"
        arguments = [str(args[0]), *[
            os.path.relpath(arg, directory) if relative and isinstance(arg, Path) else str(arg)
            for arg in args[1:]
        ]]
        return run_native(arguments, directory, self.env,
                          lambda: self.cancelled(job_id),
                          lambda message: self.store.event(job_id, job["state"], message))

    def acquire(self, job_id, source):
        self.check(job_id)
        if "local_path" in source:
            path = Path(source["local_path"])
            stat = path.stat()
            if stat.st_size != source["size"] or stat.st_mtime_ns != source["mtime_ns"]:
                raise ValueError("Selected local source changed since preflight. Import it again.")
            directory = self.store.root / "cache" / cache_key(source)
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / "source.pbf"
            manifest = directory / "manifest.json"
            if destination.exists() and manifest.exists():
                saved = json.loads(manifest.read_text(encoding="utf-8"))
                if saved.get("sha256") != self.digest(job_id, destination):
                    raise ValueError("Local input cache checksum failed. Clear the corrupt cache.")
                return destination
            partial = directory / "copy.part"
            with path.open("rb") as reader, partial.open("wb") as writer:
                for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                    self.check(job_id)
                    writer.write(chunk)
            after = path.stat()
            if after.st_size != stat.st_size or after.st_mtime_ns != stat.st_mtime_ns:
                raise ValueError("Local source changed while making an immutable processing copy.")
            if partial.stat().st_size != source["size"]:
                raise ValueError("Local input copy size mismatch.")
            digest = self.digest(job_id, partial)
            partial.replace(destination)
            manifest.write_text(json.dumps({**source, "sha256": digest}), encoding="utf-8")
            return destination
        trusted_url(source["url"])
        download_url = source.get("resolved_url", source["url"])
        trusted_url(download_url)
        key = cache_key(source)
        directory = self.store.root / "cache" / key
        directory.mkdir(parents=True, exist_ok=True)
        # All generated filenames are server owned; never trust a catalog ID as a path.
        destination = directory / ("source.pbf" if source["url"].endswith(".pbf") else "source.zip")
        manifest_path = directory / "manifest.json"
        if destination.exists() and manifest_path.exists():
            saved = json.loads(manifest_path.read_text(encoding="utf-8"))
            if saved.get("sha256") == self.digest(job_id, destination):
                self.store.event(job_id, "downloading", f"Reusing validated cache: {source['id']}")
                return destination
            raise ValueError(f"Cached source checksum failed: {source['id']}. Remove the corrupt cache.")
        partial = directory / "download.part"
        headers = {"Accept-Encoding": "identity"}
        with httpx.Client(timeout=httpx.Timeout(120, connect=30), follow_redirects=False) as client:
            current = source_headers(client, download_url)
            if (any(current[k] != source[k] for k in ("size", "etag", "modified")) or
                current["resolved_url"] != download_url):
                raise ValueError(f"Source changed since preflight: {source['id']}. Review a new preflight.")
            offset = partial.stat().st_size if partial.exists() else 0
            if offset >= source["size"]:
                offset = 0
            validator = source.get("etag") or source.get("modified")
            strong_validator = validator and not validator.startswith("W/")
            if offset and strong_validator:
                headers.update({"Range": f"bytes={offset}-", "If-Range": validator})
            else:
                offset = 0
            with source_stream(client, "GET", download_url, headers=headers) as response:
                if str(response.url) != download_url:
                    raise ValueError("Download source URL changed since preflight. Review a new preflight.")
                if offset and response.status_code == 206:
                    expected = f"bytes {offset}-"
                    if not response.headers.get("content-range", "").startswith(expected):
                        raise ValueError("Invalid source resume response.")
                elif response.status_code == 200:
                    offset = 0
                else:
                    raise ValueError("Unexpected source download response.")
                for header, field in (("etag", "etag"), ("last-modified", "modified")):
                    if source.get(field) and response.headers.get(header) != source[field]:
                        raise ValueError("Download source validator changed during transfer.")
                received = offset
                reported = 0
                with partial.open("ab" if offset else "wb") as stream:
                    for chunk in response.iter_bytes(1024 * 1024):
                        self.check(job_id)
                        if shutil.disk_usage(directory).free < len(chunk) + GIB:
                            raise ValueError("Disk space exhausted during download. Free disk and retry.")
                        stream.write(chunk)
                        received += len(chunk)
                        if received > source["size"]:
                            raise ValueError("Source exceeded its advertised size.")
                        if time.monotonic() - reported > 2:
                            self.store.event(job_id, "downloading",
                                             f"{source['id']}: {received:,} / {source['size']:,} bytes")
                            reported = time.monotonic()
        if partial.stat().st_size != source["size"]:
            raise ValueError(f"Download size mismatch: {source['id']}")
        digest = self.digest(job_id, partial)
        partial.replace(destination)
        manifest_path.write_text(json.dumps({**source, "sha256": digest}), encoding="utf-8")
        return destination

    def execute(self, job):
        job_id, spec, plan = job["id"], job["spec"], job["plan"]
        directory = self.store.directory(job_id)
        scratch = directory / "scratch"
        scratch.mkdir(exist_ok=True)
        final = directory / f"map.{spec['format']}"
        temporary = scratch / f"map.{spec['format']}"
        try:
            self.check(job_id)
            for path in (self.bin / "osmium.exe", self.bin / "java.exe", self.tools / "planetiler.jar"):
                if not path.is_file():
                    raise ValueError("Native tools missing. Run scripts\\bootstrap.ps1.")
            if shutil.disk_usage(directory).free < plan["disk_required"]:
                raise ValueError("Free disk dropped below the preflight requirement.")
            self.store.update(job_id, "downloading")
            inputs = [self.acquire(job_id, source) for source in plan["sources"]]
            auxiliary = {}
            for source in plan["auxiliary"]:
                auxiliary[source["id"]] = self.acquire(job_id, source)
            self.store.update(job_id, "validating")
            metadata = []
            sources = []
            for source, path in zip(plan["sources"], inputs, strict=True):
                info = json.loads(self.run(job_id, self.bin / "osmium.exe", "fileinfo",
                                           "-e", "--json", path))
                if not info["data"]["objects_ordered"] or info["data"]["multiple_versions"]:
                    raise ValueError("Input must be a sorted, non-history OSM snapshot.")
                metadata.append(info)
                sources.append({"id": source["id"], "url": source.get("url"),
                                "resolved_url": source.get("resolved_url"),
                                "size": path.stat().st_size, "sha256": self.digest(job_id, path),
                                "header": info["header"], "data_timestamp": info["data"]["timestamp"]})
            selected = inputs[0]
            if len(inputs) > 1:
                snapshots = [
                    info["header"]["option"].get("osmosis_replication_timestamp") for info in metadata
                ]
                if not all(snapshots) or len(set(snapshots)) != 1:
                    raise ValueError("Cannot safely merge extracts from different or unknown replication "
                                     "snapshots. Select aligned inputs or one covering parent extract.")
                self.store.update(job_id, "merging")
                selected = scratch / "merged.osm.pbf"
                self.run(job_id, self.bin / "osmium.exe", "merge", *inputs,
                         "-o", selected, "--overwrite")
                merged = json.loads(self.run(job_id, self.bin / "osmium.exe", "fileinfo",
                                             "-e", "--json", selected))
                if merged["data"]["multiple_versions"]:
                    raise ValueError("Merged source contains conflicting object versions.")
            # Node-based PBF clipping loses crossing ways and enclosing polygons with no vertex in AOI.
            self.store.update(job_id, "extracting")
            self.store.event(job_id, "extracting",
                             "Preserving complete input topology; exact extraction occurs after geometry assembly.")
            self.run(job_id, self.bin / "osmium.exe", "check-refs", selected)
            relations = scratch / "area-relations.osm.pbf"
            self.run(job_id, self.bin / "osmium.exe", "tags-filter", selected,
                     "r/type=multipolygon", "r/type=boundary", "-o", relations, "--overwrite")
            try:
                self.run(job_id, self.bin / "osmium.exe", "check-refs", "-r", relations)
            except RuntimeError as error:
                raise ValueError("Area relations have missing members. Choose complete aligned inputs "
                                 "or a larger parent extract; see reference-check logs.") from error
            self.store.update(job_id, "generating")
            import psutil
            if spec["memory_gb"] * GIB + GIB > psutil.virtual_memory().available:
                raise ValueError("Available RAM dropped below the reviewed JVM budget plus 1 GiB.")
            region = normalize_region(spec["geometry"])
            aoi = scratch / "region.wkt"
            aoi.write_text(region.wkt, encoding="utf-8")
            arguments = [
                self.bin / "java.exe", f"-Xmx{spec['memory_gb']}g",
                "-cp", self.tools / "planetiler.jar", Path(__file__).resolve().parents[2] /
                "tiles" / "RegionProfile.java",
                f"--osm-path={os.path.relpath(selected, directory)}",
                f"--aoi={aoi.relative_to(directory)}",
                f"--output={temporary.relative_to(directory)}",
                f"--tmpdir={(scratch / 'planetiler').relative_to(directory)}",
                f"--minzoom={spec['minzoom']}",
                f"--maxzoom={spec['maxzoom']}", "--threads=2", "--force",
                "--bounds=" + ",".join(str(value) for value in region.bounds),
                "--only-layers=" + ",".join(spec["layers"]),
                f"--auxiliary={'true' if auxiliary else 'false'}",
            ]
            for key, filename in [
                ("lake_centerlines", "lake_centerline.shp.zip"),
                ("water_polygons", "water-polygons-split-3857.zip"),
                ("natural_earth", "natural_earth_vector.sqlite.zip"),
            ]:
                if filename in auxiliary:
                    arguments.append(f"--{key}-path={os.path.relpath(auxiliary[filename], directory)}")
            self.run(job_id, *arguments)
            self.check(job_id)
            self.store.update(job_id, "verifying")
            artifact = validate(temporary, lambda: self.check(job_id))
            if any(abs(actual - expected) > 1e-6 for actual, expected in
                   zip(artifact["metadata"]["bounds"], region.bounds, strict=True)):
                raise ValueError("Output bounds do not match the selected region.")
            manifest = {
                "name": spec["name"], "selection": spec["geometry"], "format": spec["format"],
                "layers": spec["layers"], "minzoom": spec["minzoom"], "maxzoom": spec["maxzoom"],
                "tools": {"planetiler": "0.10.2", "osmium": "1.19.1", "java": "21"},
                "schema": "OpenMapTiles (Planetiler 0.10.2 bundled profile)",
                "attribution": artifact["metadata"]["attribution"],
                "sources": sources, "artifact": artifact,
                "auxiliary": [{"id": name, "sha256": self.digest(job_id, path)}
                              for name, path in auxiliary.items()],
                "limitations": ["MVT geometry quantization has up to one grid-unit boundary tolerance.",
                                "No elevation or 3D terrain. Country-scale resource estimates are uncalibrated."],
            }
            self.check(job_id)
            temporary.replace(final)
            manifest_temp = directory / "manifest.part"
            manifest_temp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            manifest_temp.replace(directory / "manifest.json")
            shutil.rmtree(scratch)
            self.store.update(job_id, "ready")
        except Cancelled as error:
            self.store.update(job_id, "cancelled", str(error))
        except (OSError, ValueError, RuntimeError, httpx.HTTPError, KeyError) as error:
            LOGGER.exception("Job %s failed", job_id)
            self.store.update(job_id, "failed", str(error))
