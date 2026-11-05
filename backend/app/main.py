import asyncio
import json
import logging
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager, closing
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from shapely.geometry import box

from app.catalog import GIB, Catalog
from app.geometry import describe_region, normalize_region
from app.models import LAYERS, NATURE, STANDARD, JobSpec, Submission
from app.process import WindowsJob, run_native
from app.store import TERMINAL, Store

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]


def create_app(data_root: Path | None = None, start_worker=True) -> FastAPI:
    data_root = data_root or Path(os.environ.get(
        "MAP_CREATOR_DATA", str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MapCreator")))
    data_root = data_root.resolve()
    store = Store(data_root)
    catalog = Catalog(data_root)
    token = secrets.token_urlsafe(32)
    preflights = {}
    preflight_lock = threading.Lock()
    imports_path = data_root / "local-inputs.json"
    imports = json.loads(imports_path.read_text(encoding="utf-8")) if imports_path.exists() else {}
    tools = ROOT / ".tools"

    @asynccontextmanager
    async def lifespan(app):
        process = None
        owner = WindowsJob()
        worker_log = None
        try:
            if start_worker:
                worker_log = (data_root / "worker.log").open("w", encoding="utf-8")
                process = await asyncio.to_thread(subprocess.Popen,
                    [sys.executable, "-m", "app.worker", str(data_root), str(tools)],
                    cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "backend")},
                    stdout=worker_log, stderr=subprocess.STDOUT,
                    creationflags=0x08000000 if os.name == "nt" else 0)
                owner.assign(process)
                await asyncio.sleep(0.5)
                if process.poll() is not None:
                    log_tail = (data_root / "worker.log").read_text(encoding="utf-8")[-2000:]
                    raise RuntimeError(f"Worker could not start: {log_tail}")
            app.state.worker = process
            yield
        finally:
            owner.close()
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            if worker_log:
                worker_log.close()

    app = FastAPI(title="Map Creator", lifespan=lifespan)
    app.state.store = store
    app.state.catalog = catalog
    app.state.preflights = preflights
    app.state.imports = imports

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.url.hostname
        origin = request.headers.get("origin")
        allowed_origin = f"{request.url.scheme}://{request.headers.get('host')}"
        if (host not in {"127.0.0.1", "localhost", "::1"} or
            (origin and origin != allowed_origin) or
            (request.headers.get("sec-fetch-site") == "cross-site" and
             request.url.path.startswith("/api/"))):
            return JSONResponse({"detail": "Only same-origin loopback requests are allowed."}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(
            request.headers.get("x-map-token", ""), token
        ):
            return JSONResponse({"detail": "Missing or invalid local session token."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({"detail": "Job or source not found."}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.get("/api/session")
    def session():
        return {"token": token, "layers": sorted(LAYERS), "nature": NATURE, "standard": STANDARD,
                "native_ready": all((tools / part).exists() for part in
                                    ["runtime\\Library\\bin\\osmium.exe", "runtime\\Library\\bin\\java.exe",
                                     "planetiler.jar"]),
                "data_directory": str(data_root)}

    @app.get("/api/catalog")
    def get_catalog():
        try:
            return catalog.load()
        except (OSError, ValueError, RuntimeError, httpx.HTTPError) as error:
            # HTTP/provider failures must be visible instead of a success-shaped empty catalog.
            LOGGER.exception("Could not load catalog")
            raise HTTPException(502, f"Could not load provider catalog: {error}") from error

    @app.post("/api/import")
    def import_local():
        import tkinter
        from tkinter import filedialog
        dialog = tkinter.Tk()
        dialog.withdraw()
        try:
            selected = filedialog.askopenfilenames(
                title="Choose local OSM PBF snapshots", filetypes=[("OSM PBF", "*.pbf")])
        finally:
            dialog.destroy()
        result = []
        for name in selected:
            path = Path(name).resolve(strict=True)
            source_id = "local-" + uuid.uuid4().hex
            native = tools / "runtime" / "Library" / "bin"
            env = {**os.environ, "PATH": str(native) + os.pathsep + os.environ["PATH"]}
            # Feed the header through a handle; native narrow argv cannot represent every filename.
            with path.open("rb") as source:
                info = json.loads(run_native(
                    [str(native / "osmium.exe"), "fileinfo", "--json", "--input-format=pbf", "-"],
                    data_root, env, lambda: False, lambda message: LOGGER.info("%s", message),
                    stdin=source))
            if info["header"]["with_history"] or not info["header"]["boxes"]:
                raise ValueError("Choose a non-history PBF with declared geographic bounds, "
                                 "such as a complete Geofabrik extract. Full validation runs in the worker.")
            from shapely.ops import unary_union
            coverage = unary_union([box(*bounds) for bounds in info["header"]["boxes"]])
            stat = path.stat()
            imports[source_id] = {"id": source_id, "name": path.name, "local_path": str(path),
                                  "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                                  "coverage": coverage.__geo_interface__}
            result.append({"id": source_id, "name": path.name})
        temporary = imports_path.with_suffix(".part")
        temporary.write_text(json.dumps(imports), encoding="utf-8")
        temporary.replace(imports_path)
        return result

    def make_preflight(spec: JobSpec):
        local_ids = [value for value in spec.sources if value.startswith("local-")]
        if local_ids:
            if len(local_ids) != len(spec.sources):
                raise ValueError("Choose either local inputs or provider inputs, not both.")
            import psutil
            from shapely.geometry import shape
            from shapely.ops import unary_union

            from app.catalog import AUXILIARY, PREFLIGHT_TIMEOUT, source_headers, with_cache_info
            region = normalize_region(spec.geometry)
            selected = [imports[source_id] for source_id in local_ids]
            coverage = unary_union([shape(source["coverage"]) for source in selected])
            if not coverage.covers(region):
                raise ValueError("Local inputs' coordinate bounds do not cover the selection.")
            auxiliary = []
            with httpx.Client(timeout=PREFLIGHT_TIMEOUT, follow_redirects=False) as client:
                for filename, url in AUXILIARY:
                    auxiliary.append({"id": filename, "url": url, **source_headers(client, url)})
            auxiliary = [with_cache_info(data_root, source) for source in auxiliary]
            size = sum(source["size"] for source in selected)
            required = size * 14 + sum(source["size"] for source in auxiliary) + 2 * GIB
            errors = []
            if shutil.disk_usage(data_root).free < required:
                errors.append("Insufficient free disk for these inputs.")
            if spec.memory_gb * GIB + GIB > psutil.virtual_memory().available:
                errors.append("Memory budget must leave 1 GiB available for the application.")
            if size * 0.5 > spec.memory_gb * GIB:
                errors.append("Input size requires a larger JVM memory budget.")
            return {**describe_region(region), "sources": selected, "auxiliary": auxiliary,
                    "download_bytes": sum(s["size"] for s in auxiliary if not s["cached"]),
                    "disk_required": required, "free_disk": shutil.disk_usage(data_root).free,
                    "available_memory": psutil.virtual_memory().available, "errors": errors,
                    "warnings": [("Local coordinate bounds do not prove source completeness. "
                                  "Use complete provider extracts or verified local snapshots."),
                                 "Resource estimates are uncalibrated country-scale heuristics."]}
        return catalog.preflight(spec)

    @app.post("/api/preflight")
    def preflight(spec: JobSpec):
        try:
            plan = make_preflight(spec)
        except (OSError, RuntimeError, httpx.HTTPError) as error:
            LOGGER.exception("Preflight failed")
            raise HTTPException(502, f"Preflight failed: {error}") from error
        key = uuid.uuid4().hex
        with preflight_lock:
            for expired in [k for k, item in preflights.items()
                            if time.monotonic() - item["time"] > 1800]:
                del preflights[expired]
            preflights[key] = {"time": time.monotonic(), "spec": spec.model_dump(), "plan": plan}
        public_plan = json.loads(json.dumps(plan))
        for source in public_plan["sources"]:
            source.pop("local_path", None)
            source.pop("coverage", None)
        return {"preflight_id": key, **public_plan}

    @app.post("/api/jobs", status_code=201)
    def create_job(submission: Submission):
        with preflight_lock:
            review = preflights.get(submission.preflight_id)
            if not review or time.monotonic() - review["time"] > 1800:
                raise HTTPException(409, "Preflight expired. Review sources and resources again.")
            if review["spec"] != submission.spec.model_dump() or review["plan"]["errors"]:
                raise HTTPException(409, "Configuration changed or preflight has blocking errors.")
            result = store.create(review["spec"], review["plan"])
            del preflights[submission.preflight_id]
        return public_job(result)

    def public_job(job):
        result = {key: value for key, value in job.items() if key != "plan"}
        result["sources"] = [{"id": s["id"], "name": s.get("name", s["id"])}
                             for s in job["plan"]["sources"]]
        return result

    @app.get("/api/jobs")
    def jobs():
        process = getattr(app.state, "worker", None)
        if process and process.poll() is not None:
            raise HTTPException(503, "Worker stopped unexpectedly. Restart the application; see worker.log.")
        return [public_job(job) for job in store.list()]

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str):
        return public_job(store.get(job_id))

    @app.get("/api/jobs/{job_id}/events")
    def job_events(job_id: str, after: int = 0):
        store.get(job_id)
        return store.events(job_id, max(0, after))

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        job = store.get(job_id)
        if job["state"] in TERMINAL:
            raise HTTPException(409, "Job is already finished.")
        store.cancel(job_id)
        return {"status": "cancelling"}

    @app.post("/api/jobs/{job_id}/retry", status_code=201)
    def retry(job_id: str):
        job = store.get(job_id)
        if job["state"] not in {"failed", "cancelled", "interrupted"}:
            raise HTTPException(409, "Only stopped jobs can be retried.")
        # Re-review is mandatory: provider latest URLs and available resources may have changed.
        return {"spec": job["spec"], "requires_preflight": True}

    @app.delete("/api/jobs/{job_id}/scratch")
    def cleanup_scratch(job_id: str):
        job = store.get(job_id)
        if job["state"] not in TERMINAL:
            raise HTTPException(409, "Cannot clean an active job.")
        scratch = store.directory(job_id) / "scratch"
        if scratch.exists():
            shutil.rmtree(scratch)
        return {"status": "cleaned"}

    @app.delete("/api/cache")
    def cleanup_cache():
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("""SELECT count(*) FROM jobs
                WHERE state NOT IN ('ready','failed','cancelled','interrupted')""").fetchone()[0]
            if active:
                raise HTTPException(409, "Cannot remove cached sources while jobs are queued or running.")
            cache = data_root / "cache"
            if cache.exists():
                shutil.rmtree(cache)
        return {"status": "cleaned"}

    def ready_directory(job_id):
        job = store.get(job_id)
        if job["state"] != "ready":
            raise HTTPException(409, "Artifact is not ready.")
        return job, store.directory(job_id)

    @app.get("/api/jobs/{job_id}/manifest")
    def get_manifest(job_id: str):
        _, directory = ready_directory(job_id)
        return FileResponse(directory / "manifest.json", filename="manifest.json",
                            media_type="application/json")

    @app.api_route("/api/jobs/{job_id}/artifact", methods=["GET", "HEAD"])
    def artifact(job_id: str):
        job, directory = ready_directory(job_id)
        return FileResponse(directory / f"map.{job['spec']['format']}",
                            filename=f"map.{job['spec']['format']}",
                            media_type="application/octet-stream")

    @app.get("/api/jobs/{job_id}/tiles/{z}/{x}/{y}.pbf")
    def mbtile(job_id: str, z: int, x: int, y: int):
        job, directory = ready_directory(job_id)
        if job["spec"]["format"] != "mbtiles" or not 0 <= z <= 14 or not (
            0 <= x < (1 << z) and 0 <= y < (1 << z)
        ):
            raise HTTPException(404, "Tile not found.")
        with closing(sqlite3.connect(
            f"{(directory / 'map.mbtiles').as_uri()}?mode=ro", uri=True
        )) as db:
            row = db.execute("""SELECT tile_data FROM tiles
                              WHERE zoom_level=? AND tile_column=? AND tile_row=?""",
                             (z, x, (1 << z) - 1 - y)).fetchone()
        if row is None:
            return Response(status_code=204)
        headers = {"Content-Encoding": "gzip"} if row[0].startswith(b"\x1f\x8b") else {}
        return Response(row[0], media_type="application/vnd.mapbox-vector-tile", headers=headers)

    built = ROOT / "web" / "dist"
    if built.exists():
        app.mount("/assets", StaticFiles(directory=built / "assets"), name="assets")

        @app.get("/world.geojson")
        def world():
            return FileResponse(built / "world.geojson", media_type="application/geo+json")

        @app.get("/")
        def index():
            return FileResponse(built / "index.html")
    return app
