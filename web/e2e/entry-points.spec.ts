import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { createFakeApi, withPendingAsk, type FakeApi } from "../src/rules/fake-api";
import { mockRulesApi } from "./fixtures/rules";

/**
 * The Rules tab's scenarios (e2e/rules.spec.ts before the fold), moved to
 * where a rule now lives: an entry point of the workflow it starts, on the
 * Workflows tab's Simple view (spec c16, c29; plan t9). The stateful fake API
 * (src/rules/fake-api.ts) records every request, so each scenario asserts
 * what reached the API.
 */
const SCREENSHOT = process.env.RULES_APP_SCREENSHOT ?? "test-results/entry-point.png";

async function agentState(page: Page) {
  return JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}");
}
async function untilReady(page: Page) {
  await expect.poll(async () => (await agentState(page)).status).toBe("ready");
}
const sent = (api: FakeApi, method: string, path: string) =>
  api.calls.filter((c) => c.method === method && c.path === path);
const entry = (page: Page, name: string) => page.getByRole("group", { name: `Entry point: ${name}`, exact: true });

/** The fixture with more of its rules starting build-image, so they are its entry points too. */
function apiWith(...alsoBuildImage: string[]): FakeApi {
  const api = createFakeApi();
  for (const id of alsoBuildImage) {
    api.rules.find((r) => r.id === id)!.workflow = { id: "build-image", inputs: {} };
  }
  return api;
}

/** Open build-image's Simple view at `rule`'s entry point (expanded). */
async function openAt(page: Page, rule: string, name: string, api: FakeApi = createFakeApi()) {
  await mockRulesApi(page, api);
  await page.goto(`/workflows?id=build-image&entry=${rule}`);
  await untilReady(page);
  await expect(entry(page, name).getByRole("button", { name: `Collapse ${name}` })).toBeVisible();
  return api;
}

async function expand(page: Page, name: string): Promise<Locator> {
  const card = entry(page, name);
  await card.getByRole("button", { name: `Expand ${name}` }).click();
  await expect(card.getByRole("button", { name: `Collapse ${name}` })).toBeVisible();
  return card;
}

test.describe("Entry points (the Rules tab, folded into Workflows)", () => {
  test("the open entry point reads trigger → condition → run order → steps → then, left to right", async ({ page }) => {
    await openAt(page, "build-and-publish", "Build and publish");
    const card = entry(page, "Build and publish");
    // When: the trigger, then its condition, then its run order (the must-after card).
    await expect(card.locator(".fold-entry__trigger")).toContainText("Push to main");
    await expect(card.getByRole("list", { name: "Only if all of" })).toContainText("verdict");
    const rel = card.getByTestId("relationship");
    await expect(rel).toContainText("must run after Review on approve");
    expect(await rel.evaluate((el) => getComputedStyle(el).borderTopStyle)).toBe("dashed");
    const y = async (l: Locator) => (await l.boundingBox())!.y;
    expect(await y(card.locator(".fold-entry__trigger"))).toBeLessThan(await y(card.getByRole("list", { name: "Only if all of" })));
    expect(await y(card.getByRole("list", { name: "Only if all of" }))).toBeLessThan(await y(rel));
    // When | Steps | Then, left to right; the action is the Then's "ends here".
    const x = async (l: Locator) => (await l.boundingBox())!.x;
    const when = page.getByRole("heading", { level: 2, name: "When" });
    const steps = page.getByRole("region", { name: "Steps of this workflow" });
    const then = page.getByRole("heading", { level: 2, name: "Then" });
    expect(await x(when)).toBeLessThan(await x(steps));
    expect(await x(steps)).toBeLessThan(await x(then));
    await expect(page.getByRole("group", { name: "Ends here", exact: true })).toContainText("Publish");
    await expect(card.getByRole("button", { name: "Add condition" })).toBeVisible();
    await page.screenshot({ path: SCREENSHOT, fullPage: true });
  });

  test("(i) on the entry point: the description, keyboard, focus return, no navigation (d19)", async ({ page }) => {
    const api = await openAt(page, "build-and-publish", "Build and publish");
    const about = entry(page, "Build and publish").getByRole("button", { name: "About Build and publish" });
    await about.click();
    const panel = page.getByRole("dialog", { name: "About Build and publish" });
    await expect(panel.getByTestId("about-lines")).toHaveText("When event\nThen http.call");
    await expect(page).toHaveURL(/\/workflows\?id=build-image&entry=build-and-publish$/);
    await expect(panel).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(panel).toBeHidden();
    await expect(about).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(panel.getByTestId("about-lines")).toContainText("When ");
    await page.keyboard.press("Escape");
    await expect(about).toBeFocused();
    // GET /rules/{id}/describe, the Rules tab's endpoint: read afresh on each opening.
    expect(sent(api, "GET", "/rules/build-and-publish/describe")).toHaveLength(2);
  });

  test("toggle, edit and delete-with-undo work and reach the API", async ({ page }) => {
    const api = await openAt(page, "build-and-publish", "Build and publish", apiWith("clean-caches"));
    const sw = entry(page, "Clean caches").getByRole("switch", { name: "Clean caches enabled" });
    await sw.click();
    await expect(sw).toHaveAttribute("aria-checked", "true");
    expect(sent(api, "POST", "/rules/clean-caches/enable")).toHaveLength(1);

    const card = entry(page, "Build and publish");
    await card.getByRole("button", { name: "Edit Build and publish" }).click();
    const form = page.getByRole("form", { name: "Edit rule" });
    await form.getByLabel("Name").fill("Ship it");
    await form.getByRole("button", { name: "Save" }).click();
    await expect(entry(page, "Ship it")).toBeVisible();
    expect(sent(api, "PUT", "/rules/build-and-publish")).toHaveLength(1);

    await entry(page, "Ship it").getByRole("button", { name: "Delete Ship it" }).click();
    await expect(page.getByText("Deleted Ship it")).toBeVisible();
    await expect(entry(page, "Ship it")).toHaveCount(0);
    await page.getByRole("button", { name: "Undo" }).click();
    await expect(entry(page, "Ship it")).toHaveCount(1);
    expect(sent(api, "POST", "/rules/build-and-publish/restore")).toHaveLength(1);
  });

  test("a relationship is added with its slot, shows on both ends, is dragged to another kind, and removed", async ({ page }) => {
    const api = await openAt(page, "build-and-publish", "Build and publish", apiWith("clean-caches"));
    const card = entry(page, "Build and publish");
    await card.getByLabel("Add supersedes").selectOption("clean-caches");
    const made = card.getByTestId("relationship").filter({ hasText: "Clean caches" });
    await expect(made).toContainText("supersedes Clean caches");
    expect(sent(api, "PUT", "/rules/build-and-publish")[0].body).toMatchObject({ supersedes: ["clean-caches"] });

    // The other end: Clean caches reads it as superseded by Build and publish.
    const other = await expand(page, "Clean caches");
    await expect(other.getByTestId("relationship")).toContainText("superseded by Build and publish");
    await expect(other.getByTestId("relationship")).toHaveAttribute("data-direction", "in");

    // Drag the card (on Build and publish) from supersedes to may-run-after.
    const back = await expand(page, "Build and publish");
    // The page scrolls smoothly (tokens.css): settle the run order in view first, so the drag's
    // start and end points are measured where they stay.
    await back.getByRole("group", { name: "Order of Build and publish" }).evaluate((el) =>
      el.scrollIntoView({ block: "center", behavior: "instant" as ScrollBehavior }),
    );
    await back.getByTestId("relationship").filter({ hasText: "Clean caches" }).dragTo(back.getByTestId("drop-may_after"));
    await expect(back.getByTestId("relationship").filter({ hasText: "Clean caches" })).toContainText("may run after Clean caches");
    expect(sent(api, "PUT", "/rules/build-and-publish").at(-1)?.body).toMatchObject({
      supersedes: [],
      may_after: ["clean-caches"],
    });

    await back.getByRole("button", { name: "Remove: may run after Clean caches" }).click();
    await expect(back.getByTestId("relationship").filter({ hasText: "Clean caches" })).toHaveCount(0);
    expect(sent(api, "PUT", "/rules/build-and-publish").at(-1)?.body).toMatchObject({ may_after: [] });
  });

  test("must-after shows on both entry points and is removable from the other end", async ({ page }) => {
    const api = await openAt(page, "review-on-approve", "Review on approve", apiWith("review-on-approve"));
    const card = entry(page, "Review on approve");
    await expect(card.getByRole("button", { name: /^Run order \(1\)/ })).toBeVisible();
    await expect(card.getByTestId("relationship")).toContainText("Build and publish must run after this");
    await card.getByRole("button", { name: /^Remove/ }).click();
    await expect(card.getByTestId("relationship")).toHaveCount(0);
    expect(sent(api, "PUT", "/rules/build-and-publish")[0].body).toMatchObject({ must_after: [] });
  });

  test("a pending human ask is answered in context", async ({ page }) => {
    const api = await openAt(page, "build-and-publish", "Build and publish", withPendingAsk(createFakeApi()));
    const panel = entry(page, "Build and publish").getByRole("region", { name: "Waiting on you" });
    await expect(panel).toContainText("Ship this build to production?");
    await panel.getByRole("button", { name: "approve" }).click();
    await expect(panel).toHaveCount(0);
    expect(sent(api, "POST", "/asks/ask_1/answer")[0].body).toEqual({ answer: "approve" });
  });

  test("keyboard: Space toggles a switch, the picker adds a relationship, Escape closes the editor", async ({ page }) => {
    const api = await openAt(page, "build-and-publish", "Build and publish", apiWith("triage-bugs"));
    const sw = entry(page, "Triage bugs").getByRole("switch", { name: "Triage bugs enabled" });
    await sw.focus();
    await page.keyboard.press("Space");
    await expect(sw).toHaveAttribute("aria-checked", "false");
    expect(sent(api, "POST", "/rules/triage-bugs/disable")).toHaveLength(1);

    const card = entry(page, "Build and publish");
    await card.getByLabel("Add may run after").focus();
    await card.getByLabel("Add may run after").selectOption("triage-bugs");
    await expect(card.getByTestId("relationship").filter({ hasText: "Triage bugs" })).toContainText("may run after Triage bugs");

    await card.getByRole("button", { name: "Edit Build and publish" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("form", { name: "Edit rule" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("form", { name: "Edit rule" })).toHaveCount(0);
  });

  test("a new rule starts from 'New rule', asks 'When does this happen?', gets its workflow and grows through +", async ({ page }) => {
    const api = await mockRulesApi(page);
    await page.goto("/workflows");
    await untilReady(page);
    await page.getByRole("navigation", { name: "Workflows" }).getByRole("button", { name: "New rule" }).click();
    const form = page.getByRole("form", { name: "New rule" });
    await expect(form).toContainText("When does this happen?");
    await form.getByLabel("Name").fill("Disk is nearly full");
    await form.getByLabel("Surface").selectOption("github-app");
    await form.getByLabel("Event", { exact: true }).selectOption("github.push");
    await form.getByRole("button", { name: "Create rule" }).click();
    // D7 at once: a stepless workflow of its own, opened with the rule as its entry point.
    await expect(page).toHaveURL(/\/workflows\?id=disk-is-nearly-full/);
    await expect(page.getByRole("heading", { level: 1, name: "Disk is nearly full" })).toBeVisible();
    const card = entry(page, "Disk is nearly full");
    await expect(card.getByRole("button", { name: "Collapse Disk is nearly full" })).toBeVisible();
    expect(api.calls.filter((c) => c.method !== "GET").map((c) => `${c.method} ${c.path}`)).toEqual([
      "POST /rules",
      "POST /workflows",
      "PUT /rules/disk-is-nearly-full",
    ]);
    await card.getByRole("button", { name: "Add condition" }).click();
    const add = page.getByRole("form", { name: "Add condition" });
    await add.getByLabel("Variable").fill("free_gb");
    await add.getByLabel("Value").fill("low");
    await add.getByRole("button", { name: "Add", exact: true }).click();
    await expect(card.getByRole("list", { name: "Only if all of" })).toContainText("free_gb");
    await expect(card.getByRole("list", { name: "Only if all of" })).toContainText("low");
  });

  test("axe: no serious or critical violations while editing, with asks and relationships", async ({ page }) => {
    await openAt(page, "build-and-publish", "Build and publish", withPendingAsk(createFakeApi()));
    const card = entry(page, "Build and publish");
    await expect(card.getByRole("region", { name: "Waiting on you" })).toBeVisible();
    await card.getByRole("button", { name: "Edit Build and publish" }).click();
    await card.getByRole("button", { name: "Add condition" }).click();
    const results = await new AxeBuilder({ page }).analyze();
    const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${v.id} — ${v.help}: ${v.nodes.map((n) => n.target).join(" ")}`)).toEqual([]);
    expect((await agentState(page)).errors).toEqual([]);
  });

  test("prefers-reduced-motion disables the entry point's transitions", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openAt(page, "build-and-publish", "Build and publish");
    const card = entry(page, "Build and publish");
    for (const el of [
      card.getByRole("switch", { name: "Build and publish enabled" }),
      card.getByTestId("drop-must_after"),
      card.getByRole("button", { name: "Collapse Build and publish" }),
    ]) {
      const d = await el.evaluate((n) => getComputedStyle(n).transitionDuration);
      expect(d.split(",").every((x) => parseFloat(x) <= 0.00001)).toBe(true);
    }
  });
});
