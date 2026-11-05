import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import maplibregl, { type Map as LibreMap } from "maplibre-gl";
import { Protocol } from "pmtiles";
import { TerraDraw, TerraDrawPolygonMode, TerraDrawRectangleMode, TerraDrawSelectMode } from "terra-draw";
import { TerraDrawMapLibreGLAdapter } from "terra-draw-maplibre-gl-adapter";
import type { Geometry, Polygon, MultiPolygon, FeatureCollection } from "geojson";
import { overview, tileStyle } from "./style";
import "maplibre-gl/dist/maplibre-gl.css";
import "./style.css";

type Spec = { name: string; geometry: Geometry; minzoom: number; maxzoom: number; layers: string[];
  format: "pmtiles" | "mbtiles"; memory_gb: number; sources: string[] };
type Job = { id: string; name: string; state: string; error: string | null; spec: Spec; created: number };
type Plan = { preflight_id: string; area_km2: number; geometry: Geometry; bounds: number[];
  sources: { id: string; name: string; size: number; cached?: boolean }[]; auxiliary: { id: string; size: number; cached?: boolean }[];
  download_bytes: number; disk_required: number; errors: string[]; warnings: string[] };
type Session = { token: string; layers: string[]; nature: string[]; standard: string[]; native_ready: boolean };
type Event = { seq: number; message: string; stage: string };
const terminal = new Set(["ready", "failed", "cancelled", "interrupted"]);
const protocol = new Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);
const empty: FeatureCollection = { type: "FeatureCollection", features: [] };
const bytes = (value: number) => value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(1)} GiB`
  : `${(value / 1024 ** 2).toFixed(1)} MiB`;

function App() {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<LibreMap | null>(null);
  const draw = useRef<TerraDraw | null>(null);
  const token = useRef("");
  const [session, setSession] = useState<Session | null>(null);
  const [geometry, setGeometry] = useState<Geometry | null>(null);
  const [name, setName] = useState("Untitled region");
  const [format, setFormat] = useState<Spec["format"]>("pmtiles");
  const [minzoom, setMinzoom] = useState(0);
  const [maxzoom, setMaxzoom] = useState(14);
  const [memory, setMemory] = useState(2);
  const [layers, setLayers] = useState<string[]>([]);
  const [sources, setSources] = useState<string[]>([]);
  const [localSources, setLocalSources] = useState<{ id: string; name: string }[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<Job | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [mode, setMode] = useState("select");
  const [preview, setPreview] = useState(false);
  const [previewReady, setPreviewReady] = useState(false);
  const [mapReady, setMapReady] = useState(false);
  const [inspected, setInspected] = useState("");

  async function api<T>(url: string, options: RequestInit = {}): Promise<T> {
    const response = await fetch(url, { ...options, headers: {
      "Content-Type": "application/json", "X-Map-Token": token.current, ...options.headers } });
    if (!response.ok) {
      const body = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
    }
    return response.json();
  }
  async function action(work: () => Promise<void>) {
    setBusy(true); setError("");
    try { await work(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  function spec(): Spec {
    if (!geometry) throw new Error("Draw or import a region first.");
    return { name, geometry, minzoom, maxzoom, layers, format, memory_gb: memory, sources };
  }
  useEffect(() => {
    void action(async () => {
      const info = await api<Session>("/api/session");
      token.current = info.token; setSession(info); setLayers(info.nature);
      setJobs(await api<Job[]>("/api/jobs"));
    });
  }, []);
  useEffect(() => {
    if (!container.current) return;
    const instance = new maplibregl.Map({ container: container.current, style: overview(),
      center: [22, 61], zoom: 3, renderWorldCopies: false });
    map.current = instance;
    instance.addControl(new maplibregl.NavigationControl(), "bottom-right");
    instance.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    instance.on("error", event => setError(event.error.message));
    instance.on("load", () => {
      instance.addSource("region", { type: "geojson", data: empty });
      instance.addLayer({ id: "region-fill", type: "fill", source: "region",
        paint: { "fill-color": "#256d5e", "fill-opacity": 0.15 } });
      instance.addLayer({ id: "region-line", type: "line", source: "region",
        paint: { "line-color": "#256d5e", "line-width": 2, "line-dasharray": [3, 2] } });
      const drawing = new TerraDraw({
        adapter: new TerraDrawMapLibreGLAdapter({ map: instance, coordinatePrecision: 6 }),
        modes: [new TerraDrawPolygonMode(), new TerraDrawRectangleMode(), new TerraDrawSelectMode({
          flags: { polygon: { feature: { draggable: true, coordinates: {
            draggable: true, midpoints: true, deletable: true } } },
          rectangle: { feature: { draggable: true } } },
        })],
      });
      drawing.start(); drawing.setMode("select"); draw.current = drawing;
      setMapReady(true);
      drawing.on("finish", () => {
        const polygons = drawing.getSnapshot().filter(f => f.geometry.type === "Polygon");
        if (polygons.length) {
          setGeometry({ type: "MultiPolygon", coordinates: polygons.map(
            feature => (feature.geometry as Polygon).coordinates) });
          setPlan(null);
        }
      });
      instance.on("idle", () => {
        if (instance.getSource("tiles") && instance.isSourceLoaded("tiles") &&
          instance.queryRenderedFeatures().some(feature => feature.source === "tiles")) setPreviewReady(true);
      });
      drawing.on("change", () => {
        const polygons = drawing.getSnapshot().filter(f =>
          f.geometry.type === "Polygon" && f.properties.mode !== "static");
        if (polygons.length) {
          setGeometry({ type: "MultiPolygon", coordinates: polygons.map(
            feature => (feature.geometry as Polygon).coordinates) });
          setPlan(null);
        }
      });
    });
    instance.on("click", event => {
      const properties = instance.queryRenderedFeatures(event.point)
        .find(feature => feature.source === "tiles")?.properties;
      if (properties) setInspected(JSON.stringify(properties, null, 2));
    });
    return () => { draw.current?.stop(); instance.remove(); map.current = null; };
  }, []);

  function showRegion(value: Geometry | null) {
    const instance = map.current;
    if (!instance || !instance.isStyleLoaded()) return;
    const source = instance.getSource<maplibregl.GeoJSONSource>("region");
    if (source) source.setData(value
      ? { type: "Feature", properties: {}, geometry: value } : empty);
  }
  useEffect(() => { showRegion(geometry); setPlan(null); },
    [geometry, name, format, minzoom, maxzoom, memory, layers, sources]);

  useEffect(() => {
    let disposed = false;
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const list = await api<Job[]>("/api/jobs");
          if (disposed) return;
          setJobs(list);
          if (selected) {
            const current = list.find(job => job.id === selected.id);
            if (current) setSelected(current);
            const log = await api<Event[]>(`/api/jobs/${selected.id}/events?after=0`);
            if (!disposed) setEvents(log);
          }
        } catch (e) { if (!disposed) setError(e instanceof Error ? e.message : String(e)); }
      })();
    }, 2000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [selected?.id]);

  function drawingMode(value: string) {
    if (preview) return;
    draw.current?.setMode(value); setMode(value);
  }
  function fit(value: Geometry) {
    const positions: number[][] = [];
    function walk(node: unknown) {
      if (!Array.isArray(node)) return;
      if (typeof node[0] === "number") positions.push(node as number[]);
      else node.forEach(walk);
    }
    if ("coordinates" in value) walk(value.coordinates);
    if (positions.length) {
      let longitudes = positions.map(p => p[0]);
      if (Math.max(...longitudes) - Math.min(...longitudes) > 180) longitudes = longitudes.map(x => x < 0 ? x + 360 : x);
      map.current?.fitBounds([[Math.min(...longitudes), Math.min(...positions.map(p => p[1]))],
        [Math.max(...longitudes), Math.max(...positions.map(p => p[1]))]],
      { padding: 70, maxZoom: 14, duration: 0 });
    }
  }
  async function openPreview(job: Job) {
    const manifest = await api<{ attribution: string }>(`/api/jobs/${job.id}/manifest`);
    setPreviewReady(false);
    draw.current?.stop();
    const base = `${location.origin}/api/jobs/${job.id}`;
    map.current?.setStyle(tileStyle(base, job.spec.format, job.spec.layers,
      job.spec.minzoom, job.spec.maxzoom, manifest.attribution));
    setPreview(true); setSelected(job); setInspected(""); fit(job.spec.geometry);
  }
  async function saveStyle(job: Job) {
    const manifest = await api<{ attribution: string }>(`/api/jobs/${job.id}/manifest`);
    const style = tileStyle(`${location.origin}/api/jobs/${job.id}`, job.spec.format, job.spec.layers,
      job.spec.minzoom, job.spec.maxzoom, manifest.attribution);
    const url = URL.createObjectURL(new Blob([JSON.stringify(style, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = "map.style.json"; anchor.click(); URL.revokeObjectURL(url);
  }
  function returnToSelection() {
    // Reload reinstates drawing controls and current persisted job history without stale style handlers.
    location.reload();
  }

  return <div className="app">
    <header className="topbar"><div className="brand"><span className="brand-icon">F</span>
      <strong>fieldwork<span>OSM TILE WORKSHOP</span></strong></div>
      <div className="top-note"><span className="online-dot" /> Native & local <i /> Your data stays on this computer</div>
      <span className="version">WORKSPACE / 01</span></header>
    <aside className="sidebar">
      <div className="intro"><span className="eyebrow">FROM OPEN DATA TO YOUR NEXT MAP</span>
        <h1>A little world.<br />Made yours.</h1>
        <p>Select a place. Shape its layers.<br />Take a vector map with you.</p></div>
      {error && <div className="notice error" role="alert"><strong>Something needs attention</strong>
        <p>{error}</p><button className="text-button" onClick={() => setError("")}>Dismiss</button></div>}
      {session && !session.native_ready && <div className="notice error">Native tools are missing.
        Run <code>scripts\bootstrap.ps1</code> before generating tiles.</div>}
      {preview ? <section className="section">
        <span className="step">ARTIFACT PREVIEW</span><h2>{selected?.name}</h2>
        <p>This map reads the completed {selected?.spec.format.toUpperCase()} archive. Click a feature to inspect its tags.</p>
        <p role="status">{previewReady ? "Archive tiles loaded" : "Loading archive tiles. Zoom in if below the archive's minimum zoom."}</p>
        <div className="layer-list">{selected?.spec.layers.map(layer =>
          <label key={layer}><input type="checkbox" defaultChecked onChange={event => {
            if (map.current?.getLayer(layer)) map.current.setLayoutProperty(layer, "visibility", event.target.checked ? "visible" : "none");
          }} />{layer.replaceAll("_", " ")}</label>)}</div>
        {inspected && <pre className="inspect">{inspected}</pre>}
        <button className="secondary wide" onClick={returnToSelection}>Back to region selection</button>
      </section> : <>
        <section className="section"><span className="step">01 / YOUR REGION</span><h2>Where are we going?</h2>
          <label className="field">Map name<input value={name} onChange={e => setName(e.target.value)} maxLength={100} /></label>
          <div className="segmented">{["rectangle", "polygon", "select"].map(value =>
            <button key={value} disabled={!mapReady} className={mode === value ? "active" : ""} onClick={() => drawingMode(value)}>
              {value === "select" ? "Edit" : value === "rectangle" ? "Rectangle" : "Polygon"}</button>)}</div>
          <p className="hint">Draw across borders. Double-click to finish a polygon.</p>
          <div className="row"><label className="file-button">Import GeoJSON<input type="file" disabled={!mapReady} accept=".json,.geojson" onChange={e => {
            const file = e.target.files?.[0];
            if (!file) return;
            void action(async () => {
              if (file.size > 1_000_000) throw new Error("GeoJSON must be smaller than 1 MB.");
              const parsed = JSON.parse(await file.text());
              const value = parsed.type === "Feature" ? parsed.geometry : parsed;
              if (!["Polygon", "MultiPolygon"].includes(value?.type)) throw new Error("Import a Polygon or MultiPolygon.");
              draw.current?.clear(); setGeometry(value); fit(value);
            });
          }} /></label><button className="text-button" onClick={() => {
            draw.current?.clear(); setGeometry(null); setPlan(null);
          }}>Clear region</button></div>
          <button className="secondary wide import-pbf" disabled={busy || !mapReady} onClick={() => void action(async () => {
            const imported = await api<{ id: string; name: string; coverage: Polygon | MultiPolygon; selection?: Polygon | MultiPolygon }[]>("/api/import", { method: "POST" });
            if (imported.length) {
              setLocalSources(imported); setSources(imported.map(item => item.id));
              if (!geometry) {
                const coverage = imported[0].selection ?? imported[0].coverage;
                draw.current?.clear(); setGeometry(coverage); fit(coverage);
              }
            }
          })}>Choose local .osm.pbf files</button>
          <p className="hint">No external map-data loading. Choose complete local snapshots, then draw or import a smaller region if needed.</p>
          {localSources.length > 0 && <p className="hint">{localSources.map(item => item.name).join(", ")}</p>}
          <div className="region-status"><span className={geometry ? "online-dot" : "empty-dot"} />
            {geometry ? "Region selected · ready to review" : "No region selected yet"}</div>
        </section>
        <section className="section"><span className="step">02 / MAP CONTENT</span><h2>Keep what matters.</h2>
          <div className="segmented"><button onClick={() => setLayers(session?.nature ?? [])}>Natural features</button>
            <button onClick={() => setLayers(session?.standard ?? [])}>Standard map</button></div>
          <p className="hint">Local OSM land-use, forests, parks & inland water. No elevation, ocean fills, external lake centerlines or generalized basemap data.</p>
          <div className="layer-list">{session?.layers.map(layer => <label key={layer}>
            <input type="checkbox" checked={layers.includes(layer)} onChange={e =>
              setLayers(e.target.checked ? [...layers, layer] : layers.filter(value => value !== layer))} />
            {layer.replaceAll("_", " ")}</label>)}</div>
          <div className="row fields"><label className="field">Min zoom<input type="number" min={0} max={14}
            value={minzoom} onChange={e => setMinzoom(Number(e.target.value))} /></label>
            <label className="field">Max zoom<input type="number" min={0} max={14}
              value={maxzoom} onChange={e => setMaxzoom(Number(e.target.value))} /></label>
            <label className="field">JVM RAM (GiB)<input type="number" min={1} max={256}
              value={memory} onChange={e => setMemory(Number(e.target.value))} /></label></div>
        </section>
        <section className="section"><span className="step">03 / TAKE IT WITH YOU</span><h2>One region. One file.</h2>
          <div className="format-options">{(["pmtiles", "mbtiles"] as const).map(value =>
            <button key={value} className={format === value ? "active" : ""} onClick={() => setFormat(value)}>
              <strong>{value.toUpperCase()}</strong><span>{value === "pmtiles" ? "Portable · range-readable" : "SQLite · GIS-friendly"}</span></button>)}</div>
          <button className="primary wide" disabled={busy || !geometry || !layers.length || !sources.length} onClick={() => void action(async () => {
            const result = await api<Plan>("/api/preflight", { method: "POST", body: JSON.stringify(spec()) });
            setPlan(result); showRegion(result.geometry);
          })}>{busy ? "Working…" : "Review sources & resources"} <span>→</span></button>
          {plan && <div className="review"><div className="review-heading">EXTRACTION REVIEW<span>{plan.area_km2.toLocaleString(undefined, { maximumFractionDigits: 0 })} km²</span></div>
            <div className="metrics"><div><strong>{bytes(plan.download_bytes)}</strong><span>source downloads</span></div>
              <div><strong>{bytes(plan.disk_required)}</strong><span>estimated free disk needed</span></div></div>
            <h3>{plan.sources.length} OSM source{plan.sources.length > 1 ? "s" : ""}</h3>
            {plan.sources.map(source => <p key={source.id}>{source.name}{source.cached ? " · cached" : ""}
              <strong>{bytes(source.size)}</strong></p>)}
            <p className="hint">Local files only · no external dataset downloads. A validated immutable processing copy is reused when unchanged.</p>
            {plan.warnings.map(value => <p className="hint" key={value}>{value}</p>)}
            {plan.errors.map(value => <p className="error-text" key={value}>{value}</p>)}
            <button className="primary wide" disabled={busy || plan.errors.length > 0 || !session?.native_ready}
              onClick={() => void action(async () => {
                const job = await api<Job>("/api/jobs", { method: "POST", body: JSON.stringify({
                  spec: spec(), preflight_id: plan.preflight_id }) });
                setSelected(job); setEvents([]); setPlan(null); setJobs(await api("/api/jobs"));
              })}>Generate from local files</button>
          </div>}
        </section></>}
      <section className="section history"><span className="step">WORKSPACE / RECENT JOBS</span>
        {jobs.length === 0 && <p className="hint">Your maps will live here. Long jobs keep working while this tab is closed.</p>}
        {jobs.map(job => <button key={job.id} className={`job-card ${job.id === selected?.id ? "selected" : ""}`}
          onClick={() => { setSelected(job); setEvents([]); }}><span><strong>{job.name}</strong>
            <small>{job.spec.format.toUpperCase()} · z{job.spec.minzoom}–{job.spec.maxzoom}</small></span>
          <em className={job.state}>{job.state}</em></button>)}
        <button className="text-button" disabled={busy || jobs.some(job => !terminal.has(job.state))}
          onClick={() => void action(async () => {
            if (window.confirm("Remove cached processing copies? Original local files and completed map exports are kept."))
              await api("/api/cache", { method: "DELETE" });
          })}>Clear local processing cache</button>
      </section>
      <footer>Built from OpenStreetMap. Made on your machine.</footer>
    </aside>
    <main className="map-panel"><div ref={container} className="map" />
      <div className="map-caption"><span>{preview ? "YOUR GENERATED MAP" : "THE WORLD IS YOUR CANVAS"}</span>
        <strong>{preview ? selected?.name : "Find your field of view."}</strong>
        {!preview && <small>Offline overview · Natural Earth</small>}</div>
      {!geometry && !preview && <div className="map-tip">Choose Rectangle or Polygon to mark a region</div>}
      {selected && <div className="job-panel"><div className="job-heading"><div><span className="eyebrow">LOCAL PIPELINE</span>
        <h2>{selected.name}</h2></div><span className={`state ${selected.state}`}>{selected.state}</span></div>
        {selected.error && <p className="error-text">{selected.error}</p>}
        <div className="job-actions">
          {!terminal.has(selected.state) && <button className="secondary" onClick={() => void action(async () => {
            await api(`/api/jobs/${selected.id}/cancel`, { method: "POST" });
          })}>Cancel job</button>}
          {selected.state === "ready" && <>
            <button className="primary" onClick={() => void action(() => openPreview(selected))}>Preview map</button>
            <a className="secondary" href={`/api/jobs/${selected.id}/artifact`}>Export {selected.spec.format.toUpperCase()}</a>
            <a className="text-button" href={`/api/jobs/${selected.id}/manifest`}>Provenance</a>
            <button className="text-button" onClick={() => void action(() => saveStyle(selected))}>Save style</button>
          </>}
          {["failed", "interrupted", "cancelled"].includes(selected.state) && <>
            <button className="secondary" onClick={() => void action(async () => {
              const retry = await api<{ spec: Spec }>(`/api/jobs/${selected.id}/retry`, { method: "POST" });
              setGeometry(retry.spec.geometry); setName(retry.spec.name); setFormat(retry.spec.format);
              setLayers(retry.spec.layers); setSources(retry.spec.sources); setMinzoom(retry.spec.minzoom);
              setMaxzoom(retry.spec.maxzoom); setMemory(retry.spec.memory_gb); setPlan(null);
              if (preview) returnToSelection();
            })}>Review & retry</button>
            <button className="text-button" onClick={() => void action(async () => {
              await api(`/api/jobs/${selected.id}/scratch`, { method: "DELETE" });
            })}>Clean scratch files</button></>}
        </div>
        <details><summary>Processing log <span>{events.length} recent events</span></summary>
          <pre className="logs">{events.map(event => `[${event.stage}] ${event.message}`).join("\n") || "Waiting for worker…"}</pre></details>
      </div>}
    </main>
  </div>;
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
