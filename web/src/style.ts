import type { LayerSpecification, StyleSpecification } from "maplibre-gl";

export function overview(): StyleSpecification {
  return {
    version: 8,
    sources: { world: { type: "geojson", data: "/world.geojson", attribution: "Natural Earth (public domain)" } },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#ccdce0" } },
      { id: "land", type: "fill", source: "world", paint: { "fill-color": "#eef0df" } },
      { id: "coast", type: "line", source: "world", paint: { "line-color": "#9cbbb8", "line-width": 0.6 } },
    ],
  };
}

export function tileStyle(base: string, format: "pmtiles" | "mbtiles", layers: string[],
  minzoom: number, maxzoom: number, attribution: string): StyleSpecification {
  const styles: Record<string, Partial<LayerSpecification>> = {
    water: { type: "fill", paint: { "fill-color": "#a7cdd4" } },
    landcover: { type: "fill", paint: { "fill-color": "#b2cba8", "fill-opacity": 0.8 } },
    landuse: { type: "fill", paint: { "fill-color": "#d9d4b8", "fill-opacity": 0.65 } },
    park: { type: "fill", paint: { "fill-color": "#b0ce9f", "fill-opacity": 0.7 } },
    building: { type: "fill", paint: { "fill-color": "#bfad9d", "fill-outline-color": "#ad9d91" } },
    waterway: { type: "line", paint: { "line-color": "#72b0c1", "line-width": 1.5 } },
    transportation: { type: "line", paint: { "line-color": "#fff5d7", "line-width": 2 } },
    boundary: { type: "line", paint: { "line-color": "#9b849e", "line-width": 1, "line-dasharray": [4, 3] } },
  };
  const output: LayerSpecification[] = [
    { id: "background", type: "background", paint: { "background-color": "#f0efdf" } },
  ];
  for (const layer of layers) {
    const style = styles[layer];
    if (style) {
      output.push({ id: layer, source: "tiles", "source-layer": layer, ...style } as LayerSpecification);
    } else {
      output.push({ id: layer, source: "tiles", "source-layer": layer, type: "circle",
        paint: { "circle-color": "#456e66", "circle-radius": 3, "circle-stroke-color": "#fff",
          "circle-stroke-width": 1 } });
    }
  }
  return {
    version: 8,
    sources: { tiles: format === "pmtiles"
      ? { type: "vector", url: `pmtiles://${base}/artifact`, attribution }
      : { type: "vector", tiles: [`${base}/tiles/{z}/{x}/{y}.pbf`], minzoom, maxzoom, attribution } },
    layers: output,
  };
}
