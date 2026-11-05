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

Data defaults to `%LOCALAPPDATA%\MapCreator`: SQLite job history, bounded logs, immutable local processing copies, scratch files, and completed archives. The application never processes a whole country inside browser memory.

## Workflow

1. Choose local `.osm.pbf` files. Their declared coverage becomes the initial selection when no region is drawn. Draw rectangles/polygons across borders or import GeoJSON Polygon/MultiPolygon, including holes, to select a smaller region. Use Edit to adjust drawn coordinates.
2. Select natural features or standard-map layers, zooms 0-14, a JVM memory budget, and PMTiles (default) or MBTiles. Only implemented OpenMapTiles layers are offered.
3. Review the local inputs, zero download bytes, approximate scratch-space requirements, memory warnings, and coverage before generating. No provider catalog, extract or auxiliary dataset is requested.
4. Follow durable job stages and logs. Cancel an active/queued job, review stopped jobs before retrying, or explicitly delete scratch/cache files when no job needs them.
5. Preview the actual completed archive, toggle layers, and click features to inspect attributes. Export the archive, provenance manifest, and inspection style. The saved style references this local job's tile endpoint; adjust its source URL when moving the archive to another server. PMTiles is a range-readable single file; MBTiles is SQLite with MVT tile data.

The offline overview is bundled public-domain Natural Earth land geometry. The completed preview uses only the generated archive: no external basemap, glyph, sprite, or tile service is used. Place/mountain/name layers are rendered as inspectable points/lines rather than relying on remotely hosted label fonts. The preview is a minimal local inspection style, not an OpenMapTiles reference cartographic style.

Local PBF inputs can be chosen using the native file picker without uploading large files through a browser. They must declare geographic bounds (as Geofabrik extracts do); lightweight header validation happens during import, while full sorting/version/reference checks run in the background worker. Their coordinate bounds do **not** prove geographic completeness; users must verify their own local inputs. The worker makes one immutable processing copy per unchanged local input and reuses it across regions/jobs after checksum verification. It does not duplicate each file per job or make a merged copy for a single input. A safe initial copy remains necessary: hard links or direct reads could change beneath a running job. Multi-input merges require matching OSM replication timestamps and reject unknown or conflicting snapshots.

## Processing and correctness

React/MapLibre/TerraDraw -> same-origin FastAPI API -> SQLite queue -> separate singleton Python worker -> native Osmium validation/merge -> Planetiler OpenMapTiles profile -> validated MVT PMTiles/MBTiles -> atomic publication -> actual-archive preview.

- Local selection, preflight, generation, preview and export need no external network. Provider UI/download jobs are disabled; previously queued provider jobs require a new local review. Existing completed archives remain exportable.
- Input bounds must cover the AOI but cannot prove completeness. Changed local files fail explicitly. Cache copies and output provenance retain SHA-256 checksums.
- Native subprocesses use argument arrays and owned Windows Job Objects. Cancellation and app shutdown kill only owned native descendants. SQLite persists stages and bounded logs.
- Do not preclip PBFs using only node positions: that can lose crossing ways or enclosing polygons without interior vertices. The engine reads complete covering inputs and extracts the selected geometry after assembly.
- The Java profile clips before rendering (preventing unsafe tile memoization across AOI boundaries) and after layer processing. Boundary tolerance is the MVT coordinate quantization, approximately one grid unit per tile, not a feature-level bounding-box approximation.
- Antimeridian AOIs normalize into small separate polygons; unsupported Mercator latitudes, self-intersections, malformed rings, coverage gaps, invalid zoom ranges, and unsupported layers fail explicitly.
- Output is published only after archive structure, metadata, attribution, checksums, and sample MVT decoding pass. An empty map is a failed job with an explanation, not a fake successful export.
- PMTiles is served with HTTP Range requests; MBTiles preview translates XYZ requests to TMS rows and preserves compression.

## Current validation boundary and blockers

Native Windows fixture generation, exact clipping including holes, cross-border merge deduplication, both archive formats, pipeline publication, Unicode local filenames/data directories, cancellation, API guards, byte ranges, TMS conversion, UI import, and actual-archive preview/export have automated checks.

**A representative large-country resource benchmark is not yet complete.** The current disk estimate reserves input/cache/merge/output overhead; the memory estimate starts from Planetiler's upstream approximately 0.5x PBF-size heuristic. These are conservative starting estimates, not measured guarantees that a country will fit a particular computer. Maximum zoom is limited to 14 until budgets are calibrated.

As of 0.1.5, map processing uses only local OSM inputs: no ocean polygons, external
lake centerlines or Natural Earth basemap dataset. OSM-derived layers remain
selectable, but low-zoom generalized detail, ocean/coastline fills and some lake
labels may be missing. Preflight and manifests report this limitation rather than
faking absent content. The small public-domain Natural Earth overview is already
bundled with the application and is not included in exported tiles.

Runtime installation/bootstrap still downloads pinned Python, Java, Osmium and
application dependencies. This is offline **map-data processing after setup**,
not an offline installer. Backend tests block external sockets/HTTP while importing
real native fixtures, preflighting, generating both formats and exporting. Native
Java runs use an unreachable HTTP/HTTPS proxy in this test. Browser tests reject
external requests throughout selection, real generation, preview and export.

PMTiles/MBTiles export does not include a standalone rendering application. For redistribution, preserve attribution and consult `THIRD_PARTY_NOTICES.md`.

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

The UI-error test mocks a failed preflight response. The full browser workflow replaces only the OS file-picker boundary with a registered real fixture; resource review, native worker generation, preview and export are real. Backend tests also exercise the native import-header path and pipeline for both formats.

Use the production build for full-stack development; the Vite server proxies `/api` to the local API, but the Python server must also be running. Never expose the API to a LAN/public interface. Its token/Origin/Host guards are designed for a local single-user workflow, not hosted authentication.
