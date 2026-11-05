# Fieldwork

A native Windows, locally hosted OpenStreetMap vector-tile workshop. Select a region, review the required inputs and resources, run a durable background job, preview its actual archive, and export PMTiles or MBTiles.

**Terrain means OSM natural features and land use, not elevation.** No DEM, hillshade, contour, or 3D terrain generation is included.

## Run on Windows

For an end-user online installer ZIP, run `.\scripts\build-installer.ps1` from a
development checkout with Node.js/npm. The ZIP in `release\` includes the prebuilt
UI, application, integrity manifest, `Install.cmd`, and `INSTALL.txt`. Extract it
and run `Install.cmd` for a per-user installation and Start menu shortcut.
Setup prompts for application and map-data directories; Enter accepts the defaults.
`-InstallDirectory`, `-DataDirectory`, and `-NonInteractive` support unattended setup.
The saved data-directory setting applies to normal launches; `MAP_CREATOR_DATA`
can override it. Paths must be separate local directories, not nested or junctions.
Uninstall removes only unchanged recorded files, preserving modified/user-added
files inside application subdirectories as well as map data.
Internet is required for pinned runtime downloads; end users do not need Node.js,
Python or Java preinstalled. This is an unsigned script installer, not an offline
bundle or signed MSI. The included instructions cover custom paths, uninstall,
preserved map data, and the production-validation limitations below.

For setup from a **source checkout** (not the installer ZIP), requires 64-bit Windows, PowerShell, Node.js 22 or newer with npm, an internet connection for setup/source downloads, and substantial free SSD space for large inputs. No Docker, WSL, global Python, global Java, or compiler is required.

From the repository root:

```powershell
.\scripts\bootstrap.ps1
.\scripts\start.ps1
```

The bootstrap installs checksummed Micromamba, a locked conda-forge Python/Java/Osmium environment, the checksummed Planetiler 0.10.2 JAR, and application dependencies under `.tools` and `web\node_modules`. It builds the UI. Native tools are not committed or redistributed in this repository. The native package lock is `tools\win-64.lock`; `tools\environment.yml` describes the original solve.

The launcher serves the application at `http://127.0.0.1:8765`. Keep its terminal open while jobs run; closing a browser tab does not stop jobs, but closing the application does. On restart, interrupted jobs are marked explicitly and can be reviewed and retried. Only one app instance may own a data directory.

Optional:

```powershell
$env:MAP_CREATOR_DATA = 'D:\MapCreatorData'
.\scripts\start.ps1 -Port 8766 -NoBrowser
```

Data defaults to `%LOCALAPPDATA%\MapCreator`: SQLite job history, bounded logs, immutable downloaded sources, scratch files, and completed archives. The application never processes a whole country inside browser memory.

## Workflow

1. Choose a provider extract, draw rectangles/polygons across borders, or import GeoJSON Polygon/MultiPolygon, including holes. Draw several polygons to select separate areas. Use Edit to adjust drawn coordinates. Imported polygons can be replaced by another import.
2. Select natural features or standard-map layers, zooms 0-14, a JVM memory budget, and PMTiles (default) or MBTiles. Only implemented OpenMapTiles layers are offered.
3. Review the resolved Geofabrik extracts, auxiliary sources, download bytes, approximate scratch-space requirements, memory warnings, and coverage before approving downloads. A small AOI can require a much larger input.
4. Follow durable job stages and logs. Cancel an active/queued job, review stopped jobs before retrying, or explicitly delete scratch/cache files when no job needs them.
5. Preview the actual completed archive, toggle layers, and click features to inspect attributes. Export the archive, provenance manifest, and inspection style. The saved style references this local job's tile endpoint; adjust its source URL when moving the archive to another server. PMTiles is a range-readable single file; MBTiles is SQLite with MVT tile data.

The offline overview is bundled public-domain Natural Earth land geometry. The completed preview uses only the generated archive: no external basemap, glyph, sprite, or tile service is used. Place/mountain/name layers are rendered as inspectable points/lines rather than relying on remotely hosted label fonts. The preview is a minimal local inspection style, not an OpenMapTiles reference cartographic style.

Local PBF inputs can be chosen using the native file picker without uploading large files through a browser. They must declare geographic bounds (as Geofabrik extracts do); lightweight header validation happens during import, while full sorting/version/reference checks run in the background worker. Their coordinate bounds do **not** prove geographic completeness; users must choose complete provider extracts or otherwise verify their own local inputs. The worker makes immutable local processing copies. Multi-input merges require matching OSM replication timestamps and reject unknown or conflicting snapshots.

## Processing and correctness

React/MapLibre/TerraDraw -> same-origin FastAPI API -> SQLite queue -> separate singleton Python worker -> native Osmium validation/merge -> Planetiler OpenMapTiles profile -> validated MVT PMTiles/MBTiles -> atomic publication -> actual-archive preview.

- Geofabrik's catalog resolves complete source coverage rather than assuming country names or bounding boxes prove coverage. Choose the smallest covering parent or a covering input set. Explicitly selected inputs must cover the AOI.
- Downloads use advertised sizes and validators, safe resume when available, immutable cache manifests, and SHA-256 verification. Changed preflight sources require another review; network errors are visible, never replaced with empty maps.
- Provider redirects are followed only through validated HTTPS source hosts, with a five-redirect limit. Preflight pins the final dated URL (for example Geofabrik's `latest` redirect); downloads and resumes use that snapshot and reject a changed destination or validator. Original and resolved URLs are retained in provenance.
- Native subprocesses use argument arrays and owned Windows Job Objects. Cancellation and app shutdown kill only owned native descendants. SQLite persists stages and bounded logs.
- Do not preclip PBFs using only node positions: that can lose crossing ways or enclosing polygons without interior vertices. The engine reads complete covering inputs and extracts the selected geometry after assembly.
- The Java profile clips before rendering (preventing unsafe tile memoization across AOI boundaries) and after layer processing. Boundary tolerance is the MVT coordinate quantization, approximately one grid unit per tile, not a feature-level bounding-box approximation.
- Antimeridian AOIs normalize into small separate polygons; unsupported Mercator latitudes, self-intersections, malformed rings, coverage gaps, invalid zoom ranges, and unsupported layers fail explicitly.
- Output is published only after archive structure, metadata, attribution, checksums, and sample MVT decoding pass. An empty map is a failed job with an explanation, not a fake successful export.
- PMTiles is served with HTTP Range requests; MBTiles preview translates XYZ requests to TMS rows and preserves compression.

## Current validation boundary and blockers

Native Windows fixture generation, exact clipping including holes, cross-border merge deduplication, both archive formats, pipeline publication, Unicode local filenames/data directories, cancellation, API guards, byte ranges, TMS conversion, UI import, and actual-archive preview/export have automated checks.

**A representative large-country resource benchmark is not yet complete.** The current disk estimate reserves input/cache/merge/output overhead; the memory estimate starts from Planetiler's upstream approximately 0.5x PBF-size heuristic. These are conservative starting estimates, not measured guarantees that a country will fit a particular computer. Maximum zoom is limited to 14 until budgets are calibrated.

During implementation, the authoritative ocean-polygon source at `osmdata.openstreetmap.de` timed out from this environment using both Python and curl. Alternate mirrors checked were unavailable or required authentication. Production preflight deliberately reports this failure rather than substituting Monaco-only fixtures, dropping ocean data, or claiming the full provider pipeline was validated. Provider-backed generation requires that source to be reachable (or a validated source-mirror decision in a follow-up). Existing validated cached jobs and native fixtures do not need that host.

An installer/signing flow, full-country performance certification, arbitrary XYZ-directory export, elevation processing, hosted multi-user operation, incremental OSM updates, and arbitrary user scripts are not included. PMTiles/MBTiles export does not include a standalone rendering application. For redistribution, preserve attribution and consult `THIRD_PARTY_NOTICES.md`.

## Development and validation

```powershell
.\scripts\test-native.ps1
.\.tools\runtime\python.exe -m pytest -q
.\.tools\runtime\python.exe -m ruff check backend
npm --prefix web test
npm --prefix web run build
```

Native fixture generation is required before native tests; otherwise those tests are explicitly skipped. Fixtures use synthetic OSM data, not a substituted production download.

Browser tests run against a separately started test instance:

```powershell
$env:MAP_CREATOR_DATA = "$PWD\.tools\browser-data"
.\scripts\seed-preview.ps1
.\scripts\start.ps1 -NoBrowser
```

In another PowerShell terminal:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.tools\playwright"
Set-Location web
npm exec -- playwright install chromium --only-shell
npm exec -- playwright test --reporter=line
```

The UI-error test mocks a failed preflight response; it does not claim to validate remote downloads. The archive-preview test reads real native generated PMTiles. Backend tests execute the real native pipeline for both formats.

Use the production build for full-stack development; the Vite server proxies `/api` to the local API, but the Python server must also be running. Never expose the API to a LAN/public interface. Its token/Origin/Host guards are designed for a local single-user workflow, not hosted authentication.
