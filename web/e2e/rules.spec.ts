import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { createFakeApi, withPendingAsk, type FakeApi } from "../src/rules/fake-api";
import { mockRulesApi } from "./fixtures/rules";

const SCREENSHOT = process.env.RULES_APP_SCREENSHOT ?? "test-results/rules-app.png";

async function agentState(page: Page) {
  return JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}");
}
async function untilReady(page: Page) {
  await expect.poll(async () => (await agentState(page)).status).toBe("ready");
}
const sent = (api: FakeApi, method: string, path: string) =>
  api.calls.filter((c) => c.method === method && c.path === path);
const row = (page: Page, id: string) => page.locator(`[data-rule-id="${id}"]`);

test.describe("Rules tab", () => {
  test("the focused rule reads must-after ghost → trigger → condition → workflow → action → +", async ({ page }) => {
    await mockRulesApi(page);
    await page.goto("/rules/build-and-publish");
    await untilReady(page);
    const order = await page
      .locator('[data-testid="relationship"], [data-testid^="stage-"], button[aria-label="Add stage"]')
      .evaluateAll((els) =>
        els.map((el) =>
          el.getAttribute("data-testid") === "relationship"
            ? "ghost"
            : (el.getAttribute("data-testid") ?? "plus"),
        ),
      );
    expect(order).toEqual([
      "ghost",
      "stage-trigger",
      "stage-condition",
      "stage-workflow",
      "stage-action",
      "plus",
    ]);
    // The ghost is dashed, the workflow solid with its offset shadow (board shapes).
    const ghost = page.getByTestId("relationship");
    expect(await ghost.evaluate((el) => getComputedStyle(el).borderTopStyle)).toBe("dashed");
    const ws = page.getByTestId("stage-workflow");
    expect(await ws.evaluate((el) => getComputedStyle(el).boxShadow)).not.toBe("none");
    await page.screenshot({ path: SCREENSHOT, fullPage: true });
  });

  test("toggle, edit and delete-with-undo work and reach the API", async ({ page }) => {
    const api = await mockRulesApi(page);
    await page.goto("/rules/build-and-publish");
    await untilReady(page);

    const sw = page.getByRole("switch", { name: "Clean caches enabled" });
    await sw.click();
    await expect(sw).toHaveAttribute("aria-checked", "true");
    expect(sent(api, "POST", "/rules/clean-caches/enable")).toHaveLength(1);

    await page.getByRole("button", { name: "Edit rule" }).click();
    const form = page.getByRole("form", { name: "Edit rule" });
    await form.getByLabel("Name").fill("Ship it");
    await form.getByRole("button", { name: "Save" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Ship it" })).toBeVisible();
    expect(sent(api, "PUT", "/rules/build-and-publish")).toHaveLength(1);

    await page.getByRole("button", { name: "Delete rule" }).click();
    await expect(page.getByRole("status")).toContainText("Deleted Ship it");
    await expect(row(page, "build-and-publish")).toHaveCount(0);
    await page.getByRole("button", { name: "Undo" }).click();
    await expect(row(page, "build-and-publish")).toHaveCount(1);
    await expect(page.getByRole("heading", { level: 1, name: "Ship it" })).toBeVisible();
    expect(sent(api, "POST", "/rules/build-and-publish/restore")).toHaveLength(1);
  });

  test("a relationship is made by dragging a rule onto a slot, shows on both ends, and is removed", async ({ page }) => {
    const api = await mockRulesApi(page);
    await page.goto("/rules/train-batch");
    await untilReady(page);

    await row(page, "clean-caches").dragTo(page.getByTestId("drop-supersedes"));
    await expect(page.getByTestId("relationship")).toContainText("supersedes Clean caches");
    expect(sent(api, "PUT", "/rules/train-batch")[0].body).toMatchObject({
      supersedes: ["clean-caches"],
    });
    await expect(row(page, "clean-caches").getByTestId("row-badge")).toHaveText("superseded");

    // The other end: Clean caches shows the inverse badge.
    await row(page, "clean-caches").getByRole("link").click();
    await expect(page.getByTestId("relationship")).toContainText("superseded by Train batch");
    await expect(page.getByTestId("relationship")).toHaveAttribute("data-direction", "in");

    // Drag the card (on Train batch) from supersedes to may-run-after: direct manipulation of the kind.
    await row(page, "train-batch").getByRole("link").click();
    await page.getByTestId("relationship").dragTo(page.getByTestId("drop-may_after"));
    await expect(page.getByTestId("relationship")).toContainText("may run after Clean caches");
    expect(sent(api, "PUT", "/rules/train-batch").at(-1)?.body).toMatchObject({
      supersedes: [],
      may_after: ["clean-caches"],
    });

    await page.getByTestId("relationship").getByRole("button", { name: /^Remove/ }).click();
    await expect(page.getByTestId("relationship")).toHaveCount(0);
    expect(sent(api, "PUT", "/rules/train-batch").at(-1)?.body).toMatchObject({ may_after: [] });
  });

  test("must-after shows as a badge on both rules and is editable from either end", async ({ page }) => {
    const api = await mockRulesApi(page);
    await page.goto("/rules/review-on-approve");
    await untilReady(page);
    await expect(page.getByTestId("relationship")).toContainText(
      "Build and publish must run after this",
    );
    await expect(row(page, "build-and-publish").getByTestId("row-badge")).toHaveText("waits for this");
    await page.getByTestId("relationship").getByRole("button", { name: /^Remove/ }).click();
    await expect(page.getByTestId("relationship")).toHaveCount(0);
    expect(sent(api, "PUT", "/rules/build-and-publish")[0].body).toMatchObject({ must_after: [] });
  });

  test("a pending human ask is answered in context", async ({ page }) => {
    const api = withPendingAsk(createFakeApi());
    await mockRulesApi(page, api);
    await page.goto("/rules/build-and-publish");
    await untilReady(page);
    const panel = page.getByRole("region", { name: "Waiting on you" });
    await expect(panel).toContainText("Ship this build to production?");
    await panel.getByRole("button", { name: "approve" }).click();
    await expect(panel).toHaveCount(0);
    expect(sent(api, "POST", "/asks/ask_1/answer")[0].body).toEqual({ answer: "approve" });
  });

  test("keyboard: Space toggles a switch, the picker adds a relationship, Escape closes the editor", async ({ page }) => {
    const api = await mockRulesApi(page);
    await page.goto("/rules/train-batch");
    await untilReady(page);
    const sw = page.getByRole("switch", { name: "Triage bugs enabled" });
    await sw.focus();
    await page.keyboard.press("Space");
    await expect(sw).toHaveAttribute("aria-checked", "false");
    expect(sent(api, "POST", "/rules/triage-bugs/disable")).toHaveLength(1);

    await page.getByLabel("Add may run after").selectOption("triage-bugs");
    await expect(page.getByTestId("relationship")).toContainText("may run after Triage bugs");

    await page.getByRole("button", { name: "Edit rule" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("form", { name: "Edit rule" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("form", { name: "Edit rule" })).toHaveCount(0);
  });

  test("a new rule starts from 'When does this happen?' and grows through +", async ({ page }) => {
    await mockRulesApi(page);
    await page.goto("/rules");
    await untilReady(page);
    await page.getByRole("button", { name: /When does this happen\?/ }).click();
    const form = page.getByRole("form", { name: "New rule" });
    await form.getByLabel("Trigger").fill("Disk is nearly full");
    await form.getByRole("button", { name: "Create rule" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Disk is nearly full" })).toBeVisible();
    await page.getByRole("button", { name: "Add stage" }).click();
    await page.getByRole("button", { name: "Add condition" }).click();
    await page.getByLabel("Variable").fill("free_gb");
    await page.getByLabel("Value").fill("low");
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await expect(page.getByTestId("stage-condition")).toContainText("free_gb is low");
  });

  test("axe: no serious or critical violations while editing, with asks and relationships", async ({ page }) => {
    await mockRulesApi(page, withPendingAsk(createFakeApi()));
    await page.goto("/rules/build-and-publish");
    await untilReady(page);
    await page.getByRole("button", { name: "Edit rule" }).click();
    await page.getByRole("button", { name: "Add stage" }).click();
    const results = await new AxeBuilder({ page }).analyze();
    const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${v.id} — ${v.help}: ${v.nodes.map((n) => n.target).join(" ")}`)).toEqual([]);
    expect((await agentState(page)).errors).toEqual([]);
  });

  test("prefers-reduced-motion disables the editor's transitions", async ({ page }) => {
    await mockRulesApi(page);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/rules/build-and-publish");
    await untilReady(page);
    for (const el of [
      page.getByRole("switch", { name: "Clean caches enabled" }),
      page.locator('[data-testid="drop-must_after"]'),
    ]) {
      const d = await el.evaluate((n) => getComputedStyle(n).transitionDuration);
      expect(d.split(",").every((x) => parseFloat(x) <= 0.00001)).toBe(true);
    }
  });
});
