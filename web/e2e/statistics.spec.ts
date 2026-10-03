import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./fixtures/api";
import { mockStatistics } from "./fixtures/statistics";

/** Where the Statistics screenshot lands, for review against the 'Chosen — Statistics' board. */
const SCREENSHOT = process.env.STATISTICS_SCREENSHOT ?? "test-results/statistics.png";

async function agentState(page: Page) {
  return JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}");
}

async function open(page: Page, opts: { status?: boolean } = {}) {
  await mockApi(page);
  await mockStatistics(page, opts);
  await page.goto("/statistics");
  await expect.poll(async () => (await agentState(page)).status).toBe("ready");
  await expect(page.getByRole("region", { exact: true, name: "orin" })).toBeVisible();
}

test.describe("Statistics tab", () => {
  test("every machine has a lane; the offline host renders offline", async ({ page }) => {
    await open(page);
    for (const name of ["spark", "thor", "spark2", "orin"]) {
      await expect(page.getByRole("region", { exact: true, name })).toBeVisible();
    }
    const orin = page.getByRole("region", { exact: true, name: "orin" });
    await expect(orin).toHaveAttribute("data-online", "false");
    await expect(orin.getByText("offline 2h")).toBeVisible();
    await expect(orin.getByText("not reachable")).toBeVisible();
    await expect(orin.getByText("n/a")).toHaveCount(3);
    // neutral machine color, not a palette slot
    expect(
      await orin.locator(".bar").first().evaluate((el) => getComputedStyle(el).backgroundColor),
    ).toBe("rgb(138, 143, 168)");
    const thor = page.getByRole("region", { exact: true, name: "thor" });
    await expect(thor).toHaveAttribute("data-online", "true");
    expect(
      await thor.locator(".bar").first().evaluate((el) => getComputedStyle(el).backgroundColor),
    ).toBe("rgb(180, 83, 31)");
    await expect(thor.getByText("+4 queued")).toBeVisible();
    await expect(thor.getByText("92%")).toBeVisible();
    await expect(thor.getByText("ok")).toBeVisible();
    await expect(thor.getByText("failed")).toBeVisible();
    const state = await agentState(page);
    expect(state.errors).toEqual([]);
    expect(state.statistics).toMatchObject({ offline: ["orin"], range: "24h", view: "lanes" });
  });

  test("time range 1h / 24h / 7d changes the bars", async ({ page }) => {
    await open(page);
    const spark = (name: RegExp) => page.getByRole("img", { name });
    await expect(spark(/^spark runs per hour, last 24 hours, peak 11$/).locator("[data-bar]")).toHaveCount(24);
    await page.getByRole("radio", { name: "1h" }).click();
    await expect(spark(/^spark runs per 5 min, last hour/).locator("[data-bar]")).toHaveCount(12);
    await page.getByRole("radio", { name: "7d" }).click();
    await expect(spark(/^spark runs per day, last 7 days/).locator("[data-bar]")).toHaveCount(7);
    // keyboard: the checked radio is the tab stop; arrows move the choice
    await page.getByRole("radio", { name: "7d" }).focus();
    await page.keyboard.press("ArrowLeft");
    await expect(page.getByRole("radio", { name: "1h" })).toHaveAttribute("aria-checked", "false");
    await expect(page.getByRole("radio", { name: "24h" })).toHaveAttribute("aria-checked", "true");
  });

  test("hovering a bar shows a tooltip", async ({ page }) => {
    await open(page);
    const bar = page
      .getByRole("img", { name: /^thor runs per hour/ })
      .locator("[data-bar]")
      .nth(16);
    await bar.hover();
    await expect(page.getByRole("tooltip")).toContainText("12 runs");
    await page.mouse.move(0, 0);
    await expect(page.getByRole("tooltip")).toHaveCount(0);
  });

  test("table view carries the same numbers", async ({ page }) => {
    await open(page);
    await page.getByRole("button", { name: "Show as table" }).click();
    const machines = page.getByRole("table", { name: "Machines" });
    await expect(machines.getByRole("row")).toHaveCount(5);
    await expect(machines.getByRole("row", { name: /orin/ })).toContainText("offline 2h");
    await expect(page.getByRole("table", { name: "Runs per hour" }).getByRole("row")).toHaveCount(25);
    await expect.poll(async () => (await agentState(page)).statistics.view).toBe("table");
    await page.getByRole("button", { name: "Show as lanes" }).click();
    await expect(page.getByRole("region", { exact: true, name: "spark" })).toBeVisible();
  });

  test("when /machines/status fails it falls back to runs and says so", async ({ page }) => {
    await open(page, { status: false });
    await expect(page.getByText(/not available right now/)).toBeVisible();
    await expect(page.getByRole("region", { exact: true, name: "thor" }).getByText("n/a")).toHaveCount(3);
  });

  test("axe: no serious or critical violations, lanes and table", async ({ page }) => {
    await open(page);
    const bad = async () =>
      (await new AxeBuilder({ page }).analyze()).violations
        .filter((v) => v.impact === "serious" || v.impact === "critical")
        .map((v) => `${v.id} — ${v.help}`);
    expect(await bad()).toEqual([]);
    await page.getByRole("button", { name: "Show as table" }).click();
    expect(await bad()).toEqual([]);
  });

  test("prefers-reduced-motion disables the range transition", async ({ page }) => {
    await open(page);
    await page.emulateMedia({ reducedMotion: "reduce" });
    const d = await page
      .getByRole("radio", { name: "1h" })
      .evaluate((el) => getComputedStyle(el).transitionDuration);
    expect(d.split(",").every((x) => parseFloat(x) <= 0.00001)).toBe(true);
  });

  test("matches the 'Chosen — Statistics' board (screenshot)", async ({ page }) => {
    await open(page);
    const h1 = page.getByRole("heading", { level: 1, name: "Statistics" });
    expect(await h1.evaluate((el) => getComputedStyle(el).fontSize)).toBe("44px");
    expect(await h1.evaluate((el) => getComputedStyle(el).fontFamily)).toContain("Fraunces");
    const lane = page.getByRole("region", { exact: true, name: "spark" });
    expect(
      await lane.locator(".lane__name").evaluate((el) => getComputedStyle(el).fontSize),
    ).toBe("26px");
    // the board's grid: 150 / 230 / flexible / 300 / 120
    const cols = await lane.evaluate((el) => getComputedStyle(el).gridTemplateColumns);
    const widths = cols.split(" ").map((w) => parseFloat(w));
    expect([widths[0], widths[1], widths[3], widths[4]]).toEqual([150, 230, 300, 120]);
    expect(await lane.evaluate((el) => getComputedStyle(el).borderRadius)).toBe("20px");
    await page.screenshot({ path: SCREENSHOT, fullPage: true });
  });
});
