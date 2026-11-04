# Data and software notices

This project downloads dependencies for the user's own installation. Native executables/JVM packages and auxiliary basemap datasets are not included in the repository. Review each dependency's installed license before redistributing binaries, bundles, tiles, or styles.

| Component/data | License / source |
|---|---|
| OpenStreetMap | ODbL 1.0; https://www.openstreetmap.org/copyright and https://opendatacommons.org/licenses/odbl/ |
| Geofabrik extracts | OSM data licensing and provider notices; https://download.geofabrik.de/ |
| OpenMapTiles schema/profile | BSD-3-Clause / attribution requirements in upstream distribution; https://github.com/openmaptiles/planetiler-openmaptiles and https://github.com/openmaptiles/openmaptiles |
| Planetiler 0.10.2 | Apache-2.0 plus bundled dependency notices; https://github.com/onthegomap/planetiler/tree/v0.10.2 |
| Osmium Tool 1.19.1 | GPL-3.0-only; https://github.com/osmcode/osmium-tool; conda-forge package/build recipe: https://github.com/conda-forge/osmium-tool-feedstock |
| OpenJDK runtime | GPL-2.0 with Classpath Exception and bundled third-party notices; conda-forge OpenJDK package license files and https://github.com/conda-forge/openjdk-feedstock |
| Micromamba | BSD-3-Clause and linked dependency notices; https://github.com/mamba-org/mamba |
| Natural Earth | Public domain; https://www.naturalearthdata.com/about/terms-of-use/ |
| OSM water polygons | OSM-derived data; https://osmdata.openstreetmap.de/data/water-polygons.html |
| Lake centerlines | Upstream source/license notices; https://github.com/acalcutt/osm-lakelines |
| MapLibre GL JS | BSD-3-Clause; https://github.com/maplibre/maplibre-gl-js |
| TerraDraw and adapter | MIT; https://github.com/JamesLMilner/terra-draw |
| PMTiles | BSD-3-Clause; https://github.com/protomaps/PMTiles |
| React | MIT; https://github.com/facebook/react |
| FastAPI / Starlette | MIT / BSD-3-Clause; installed package licenses |
| Shapely / GEOS / PROJ | BSD-3-Clause / LGPL-2.1 / MIT; installed wheel and library notices |

Every map must retain visible OpenStreetMap contributor attribution and applicable OpenMapTiles/auxiliary source attribution. The archive metadata and exported manifest retain the engine's attribution. ODbL obligations for sharing databases/derived databases are distinct from produced-work attribution; an archive export does not waive them.

The bundled overview `web\public\world.geojson` is Natural Earth's public-domain 1:110m land data from commit `ca96624a56bd078437bca8184e78163e5039ad19` of `nvkelso/natural-earth-vector`, with SHA-256 `9e0729ee253ca7d7a5c4ae9395fb1902264c5377c52e224d13dd85010e2835d9`.

Native conda packages are locked to their exact package URLs and hashes in `tools\win-64.lock`. Their installed `info`/license records and upstream feedstock recipes provide dependency notices and source/build references. If creating a future redistributed native installer, include all notices and fulfill applicable source-offer/source-availability obligations, particularly for Osmium and the JVM. This repository does not yet provide such an installer.
