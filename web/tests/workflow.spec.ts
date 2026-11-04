import { test, expect } from "@playwright/test";

test("local workspace, region import, validation errors and real archive preview", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /A little world/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Rectangle", exact: true })).toBeEnabled();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.locator('input[type="file"]').setInputFiles("../tests/fixtures/region.geojson");
  await expect(page.getByText("Region selected · ready to review")).toBeVisible();
  await page.getByLabel("Map name").fill("Border & forest study");
  await page.getByRole("button", { name: "MBTILES", exact: false }).first().click();
  await page.route("**/api/preflight", route => route.fulfill({ status: 422,
    json: { detail: "Provider source is unavailable. Try again when it is reachable." } }));
  await page.getByRole("button", { name: "Review sources & resources" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve download & generate" })).toHaveCount(0);
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
