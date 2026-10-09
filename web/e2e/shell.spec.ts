import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./fixtures/api";
import { mockRulesApi } from "./fixtures/rules";

/** Where the board screenshot lands, for review against the canvas (Fold-Editor). */
const SCREENSHOT = process.env.RULES_SCREENSHOT ?? "test-results/workflows-board.png";

/** A rule's old address and where it lives now: an entry point of the workflow it starts. */
const ENTRY = "/workflows?id=build-image&entry=build-and-publish";
const TABS = ["Workflows", "Actors", "Variables", "Statistics"];

async function agentState(page: Page) {
  return JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}");
}

async function untilReady(page: Page) {
  await expect.poll(async () => (await agentState(page)).status).toBe("ready");
}

test.describe("culture-rules shell", () => {
  test("exactly four top-level tabs (Rules folded into Workflows) and no runs/history route", async ({ page }) => {
    await mockApi(page);
    await page.goto("/workflows");
    const tabs = page.getByRole("navigation", { name: "Primary" }).getByRole("link");
    await expect(tabs).toHaveText(TABS);

    for (const path of ["/runs", "/history", "/", "/no-such-page"]) {
      await page.goto(path);
      await expect(page).toHaveURL(/\/workflows$/);
      await expect(
        page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Workflows" }),
      ).toHaveAttribute("aria-current", "page");
    }
    await expect(page.getByRole("link", { name: /^(Rules|Runs|History)$/ })).toHaveCount(0);
  });

  test("#agent-state reports ready with no errors; identity from GET /whoami; an old rule link lands on its entry point", async ({ page }) => {
    const pageErrors: string[] = [];
    const consoleErrors: string[] = [];
    page.on("pageerror", (err) => pageErrors.push(err.message));
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });
    const api = await mockRulesApi(page);
    await page.goto("/rules/build-and-publish");
    await expect(page).toHaveURL(ENTRY);
    await untilReady(page);
    const state = await agentState(page);
    expect(state.errors).toEqual([]);
    expect(state.tab).toBe("workflows");
    expect(state.identity).toEqual({
      status: "signed-in",
      identity: "ori",
      kind: "sso",
      role: "admin",
    });
    expect(state.workflows).toMatchObject({ selected: "build-image", entry: "build-and-publish", view: "simple" });
    // The deprecated alias (q8), kept for one release: the same shape the Rules tab wrote.
    expect(state.rules).toMatchObject({ count: 5, selected: "build-and-publish" });
    expect(api.calls.some((c) => c.method === "GET" && c.path === "/whoami")).toBe(true);
    await expect(page.getByRole("button", { name: "Signed in as ori (SSO)" })).toBeVisible();
    expect(pageErrors).toEqual([]);
    // The fake API has no live stream (a 404 the EventSource retries); nothing else may fail.
    expect(consoleErrors.filter((e) => !/status of 404/.test(e))).toEqual([]);
  });

  test("keyboard walk: Tab reaches every tab and Enter switches to it", async ({ page }) => {
    await mockApi(page);
    await page.goto(ENTRY);
    await untilReady(page);
    const order: string[] = [];
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press("Tab");
      order.push(
        await page.evaluate(() => {
          const el = document.activeElement as HTMLElement | null;
          return el ? (el.getAttribute("aria-label") ?? el.textContent ?? "").trim() : "";
        }),
      );
    }
    // Skip link first, then the four tabs, then the identity button.
    expect(order.slice(1, 5)).toEqual(TABS);
    // Focus is visible on whatever holds it.
    const outline = await page.evaluate(
      () => getComputedStyle(document.activeElement as Element).outlineStyle,
    );
    expect(outline).not.toBe("none");

    await page.getByRole("link", { name: "Actors" }).focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/actors$/);
    await expect(page.getByRole("heading", { level: 1, name: "Actors" })).toBeVisible();
  });

  test("axe: no serious or critical violations on any tab", async ({ page }) => {
    await mockApi(page);
    for (const path of [ENTRY, "/workflows", "/workflows?entry=clean-caches", "/actors", "/variables", "/statistics"]) {
      await page.goto(path);
      await untilReady(page);
      const results = await new AxeBuilder({ page }).analyze();
      const bad = results.violations.filter(
        (v) => v.impact === "serious" || v.impact === "critical",
      );
      expect(bad.map((v) => `${path}: ${v.id} — ${v.help}`)).toEqual([]);
    }
  });

  test("prefers-reduced-motion disables transitions", async ({ page }) => {
    await mockApi(page);
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await page.goto(ENTRY);
    await untilReady(page);
    const tab = page.getByRole("link", { name: "Actors" });
    const moving = await tab.evaluate((el) => getComputedStyle(el).transitionDuration);
    expect(moving).not.toBe("0s");

    await page.emulateMedia({ reducedMotion: "reduce" });
    const still = await tab.evaluate((el) => getComputedStyle(el).transitionDuration);
    expect(still.split(",").every((d) => parseFloat(d) <= 0.00001)).toBe(true);
  });

  test("the Workflows board with an entry point open: list | board, When | Steps | Then (screenshot)", async ({ page }) => {
    await mockApi(page);
    await page.goto(ENTRY);
    await untilReady(page);
    await expect(page.getByRole("heading", { level: 1, name: "Build image" })).toBeVisible();
    // Two columns at 1280px: the list, then the board.
    const list = (await page.getByRole("navigation", { name: "Workflows" }).boundingBox())!;
    const main = (await page.getByRole("main").boundingBox())!;
    expect(list.x + list.width).toBeLessThanOrEqual(main.x);
    // The Simple view's three columns side by side: When | Steps | Then.
    const x = async (name: string) => (await page.getByRole("heading", { level: 2, name, exact: true }).boundingBox())!.x;
    const steps = (await page.getByRole("region", { name: "Steps of this workflow" }).boundingBox())!;
    expect(await x("When")).toBeLessThan(steps.x);
    expect(steps.x).toBeLessThan(await x("Then"));
    // Type scale: board h1 is Fraunces 40px, tabs 17px.
    const h1 = page.getByRole("heading", { level: 1 });
    expect(await h1.evaluate((el) => getComputedStyle(el).fontSize)).toBe("40px");
    expect(await h1.evaluate((el) => getComputedStyle(el).fontFamily)).toContain("Fraunces");
    expect(
      await page.getByRole("link", { name: "Workflows", exact: true }).evaluate((el) => getComputedStyle(el).fontSize),
    ).toBe("17px");
    // The entry point is open, and the view switch says Simple.
    await expect(page.getByRole("group", { name: "Entry point: Build and publish" })).toHaveAttribute("data-rule-id", "build-and-publish");
    await expect(page.getByRole("group", { name: "Canvas view" }).getByRole("button", { name: "Simple" })).toHaveAttribute("aria-pressed", "true");
    await page.screenshot({ path: SCREENSHOT, fullPage: true });
  });
});
