import { describe, expect, it } from "vitest";
import { overview, tileStyle } from "./style";

describe("archive preview styles", () => {
  it("uses the actual PMTiles archive without remote assets", () => {
    const style = tileStyle("http://127.0.0.1:8765/api/jobs/example", "pmtiles",
      ["water", "landcover", "place"], 0, 14, "OSM");
    expect(style.sources.tiles).toMatchObject({
      type: "vector", url: "pmtiles://http://127.0.0.1:8765/api/jobs/example/artifact" });
    expect(style.glyphs).toBeUndefined();
    expect(style.sprite).toBeUndefined();
    expect(style.layers.map(layer => layer.id)).toEqual(["background", "water", "landcover", "place"]);
  });
  it("requests MBTiles through the XYZ endpoint", () => {
    const style = tileStyle("http://127.0.0.1:8765/api/jobs/example", "mbtiles", ["water"], 3, 10, "OSM");
    expect(style.sources.tiles).toMatchObject({ type: "vector", minzoom: 3, maxzoom: 10,
      tiles: ["http://127.0.0.1:8765/api/jobs/example/tiles/{z}/{x}/{y}.pbf"] });
  });
  it("keeps the selection overview entirely local", () => {
    expect(overview().sources.world).toMatchObject({ data: "/world.geojson" });
  });
});
