import { test, expect, type Page } from "@playwright/test";

const externalRequests = new WeakMap<Page, string[]>();
test.beforeEach(async ({ page }) => {
  const external: string[] = [];
  externalRequests.set(page, external);
  await page.route("**/*", route => {
    const url = new URL(route.request().url());
    if (url.protocol === "http:" && url.hostname === "127.0.0.1") return route.continue();
    if (["http:", "https:"].includes(url.protocol)) {
      external.push(url.href);
      return route.abort("blockedbyclient");
    }
    return route.continue();
  });
});
test.afterEach(async ({ page }) => expect(externalRequests.get(page)).toEqual([]));

test("offline local input review, real generation, preview and export", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Choose local .osm.pbf files", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Browse provider extracts" })).toHaveCount(0);
  // Only the OS picker boundary is replaced; the server has registered this real fixture.
  await page.route("**/api/import", route => route.fulfill({ json: [{
    id: "local-browser-fixture", name: "input.osm.pbf",
    coverage: { type: "Polygon", coordinates: [[[-.02,0],[.12,0],[.12,.1],[-.02,.1],[-.02,0]]] },
  }] }));
  await page.getByRole("button", { name: "Choose local .osm.pbf files", exact: true }).click();
  await expect(page.getByText("Region selected · ready to review")).toBeVisible();
  await page.getByRole("button", { name: "Clear region", exact: true }).click();
  await page.locator('input[type="file"]').setInputFiles("../tests/fixtures/region.geojson");
  await page.getByLabel("Map name").fill("Offline generated map");
  await page.getByLabel("Min zoom").fill("12");
  await page.getByLabel("JVM RAM").fill("1");
  await page.getByRole("button", { name: "Review sources & resources" }).click();
  await expect(page.getByText("Local files only · no external dataset downloads.", { exact: false })).toBeVisible();
  await expect(page.getByText("Local OSM only:", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Generate from local files", exact: true }).click();
  await expect(page.locator(".job-panel .state")).toHaveText("ready", { timeout: 45_000 });
  await page.getByRole("button", { name: "Preview map", exact: true }).click();
  await expect(page.getByText("Archive tiles loaded", { exact: true })).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "Export PMTILES", exact: true }).click();
  expect((await download).suggestedFilename()).toBe("map.pmtiles");
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("local workspace, region import, validation errors and real archive preview", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /A little world/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Rectangle", exact: true })).toBeEnabled();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.route("**/api/import", route => route.fulfill({ json: [{
    id: "local-ui", name: "fixture.osm.pbf",
    coverage: { type: "Polygon", coordinates: [[[0,0],[1,0],[1,1],[0,1],[0,0]]] },
  }] }));
  await page.getByRole("button", { name: "Choose local .osm.pbf files", exact: true }).click();
  await page.locator('input[type="file"]').setInputFiles("../tests/fixtures/region.geojson");
  await expect(page.getByText("Region selected · ready to review")).toBeVisible();
  await page.getByLabel("Map name").fill("Border & forest study");
  await page.getByRole("button", { name: "MBTILES", exact: false }).first().click();
  await page.route("**/api/preflight", route => route.fulfill({ status: 422,
    json: { detail: "Local inputs do not cover the selection." } }));
  await page.getByRole("button", { name: "Review sources & resources" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("button", { name: "Generate from local files" })).toHaveCount(0);
  await page.getByRole("button", { name: "Dismiss" }).click();
  await page.screenshot({ path: "../.tools/workspace.png", fullPage: true });
  expect(errors).toEqual([]);
});

for (const format of ["pmtiles", "mbtiles"]) test(`native ${format} renders, inspects and exports`, async ({ page }) => {
  await page.goto("/");
  const fixture = page.getByRole("button", { name: new RegExp(`Native fixture ${format}`) }).first();
  await expect(fixture).toBeVisible();
  await fixture.click();
  await page.getByRole("button", { name: "Preview map" }).click();
  await expect(page.getByText("ARTIFACT PREVIEW", { exact: true })).toBeVisible();
  await expect(page.getByText("Archive tiles loaded", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.locator("canvas").click({ position: { x: 250, y: 300 } });
  await expect(page.locator(".inspect")).toContainText("class");
  await page.getByRole("checkbox", { name: "landcover", exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "landcover", exact: true }).check();
  const styleDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Save style", exact: true }).click();
  expect((await styleDownload).suggestedFilename()).toBe("map.style.json");
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: `Export ${format.toUpperCase()}` }).click();
  expect((await download).suggestedFilename()).toBe(`map.${format}`);
  await page.screenshot({ path: `../.tools/${format}-preview.png`, fullPage: true });
});
