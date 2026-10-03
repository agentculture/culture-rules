import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./fixtures/api";

/** Where the Rules screenshot lands, for review against the 'Chosen — Rules' board. */
const SCREENSHOT = process.env.RULES_SCREENSHOT ?? "test-results/rules.png";

async function agentState(page: Page) {
  return JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}");
}

async function untilReady(page: Page) {
  await expect.poll(async () => (await agentState(page)).status).toBe("ready");
}

test.describe("culture-rules shell", () => {
  test("exactly four top-level tabs and no runs/history route", async ({ page }) => {
    await mockApi(page);
    await page.goto("/rules");
    const tabs = page.getByRole("navigation", { name: "Primary" }).getByRole("link");
    await expect(tabs).toHaveText(["Rules", "Workflows", "Actors", "Statistics"]);

    for (const path of ["/runs", "/history"]) {
      await page.goto(path);
      await expect(page).toHaveURL(/\/rules(\/|$)/);
      await expect(
        page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Rules" }),
      ).toHaveAttribute("aria-current", "page");
    }
    await expect(page.getByRole("link", { name: /^(Runs|History)$/ })).toHaveCount(0);
  });

  test("#agent-state reports ready with no errors; identity from GET /whoami", async ({ page }) => {
    const pageErrors: string[] = [];
    const consoleErrors: string[] = [];
    page.on("pageerror", (err) => pageErrors.push(err.message));
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });
    const calls = await mockApi(page);
    await page.goto("/rules/build-and-publish");
    await untilReady(page);
    const state = await agentState(page);
    expect(state.errors).toEqual([]);
    expect(state.identity).toMatchObject({ status: "signed-in", subject: "ori", mocked: false });
    expect(calls).toContain("GET /api/whoami");
    await expect(page.getByRole("button", { name: "Signed in as ori (SSO)" })).toBeVisible();
    expect(pageErrors).toEqual([]);
    expect(consoleErrors).toEqual([]);
  });

  test("keyboard walk: Tab reaches every tab and Enter switches to it", async ({ page }) => {
    await mockApi(page);
    await page.goto("/rules/build-and-publish");
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
    expect(order.slice(1, 5)).toEqual(["Rules", "Workflows", "Actors", "Statistics"]);
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
    for (const path of ["/rules/build-and-publish", "/workflows", "/actors", "/statistics"]) {
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
    await page.goto("/rules/build-and-publish");
    await untilReady(page);
    const tab = page.getByRole("link", { name: "Workflows" });
    const moving = await tab.evaluate((el) => getComputedStyle(el).transitionDuration);
    expect(moving).not.toBe("0s");

    await page.emulateMedia({ reducedMotion: "reduce" });
    const still = await tab.evaluate((el) => getComputedStyle(el).transitionDuration);
    expect(still.split(",").every((d) => parseFloat(d) <= 0.00001)).toBe(true);
  });

  test("Rules tab matches the 'Chosen — Rules' board layout (screenshot)", async ({ page }) => {
    await mockApi(page);
    await page.goto("/rules/build-and-publish");
    await untilReady(page);
    await expect(page.getByRole("heading", { level: 1, name: "Build and publish" })).toBeVisible();
    // Three columns side by side at the board's 1280px width: list | stages | last runs.
    const list = await page.getByRole("navigation", { name: "Rules" }).boundingBox();
    const main = await page.getByRole("main").boundingBox();
    const aside = await page.getByRole("complementary", { name: "Last runs" }).boundingBox();
    expect(list && main && aside).toBeTruthy();
    expect(list!.x + list!.width).toBeLessThanOrEqual(main!.x);
    expect(main!.x + main!.width).toBeLessThanOrEqual(aside!.x);
    // Stage stack is centred and capped at the board's 620px.
    const trigger = await page.getByTestId("stage-trigger").boundingBox();
    expect(trigger!.width).toBeLessThanOrEqual(620);
    // Type scale: board h1 is Fraunces 40px, stage labels 26px, tabs 17px.
    const h1 = page.getByRole("heading", { level: 1 });
    expect(await h1.evaluate((el) => getComputedStyle(el).fontSize)).toBe("40px");
    expect(await h1.evaluate((el) => getComputedStyle(el).fontFamily)).toContain("Fraunces");
    expect(
      await page
        .getByTestId("stage-trigger")
        .locator(".stage__label")
        .evaluate((el) => getComputedStyle(el).fontSize),
    ).toBe("26px");
    expect(
      await page
        .getByRole("link", { name: "Rules", exact: true })
        .evaluate((el) => getComputedStyle(el).fontSize),
    ).toBe("17px");
    // Machine colors: thor is the amber slot on the selected rule's dot.
    const dot = page.locator('[data-rule-id="build-and-publish"] .machine-dot');
    expect(await dot.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe(
      "rgb(180, 83, 31)",
    );
    await page.screenshot({ path: SCREENSHOT, fullPage: true });
  });
});
