import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { mockApi } from "./fixtures/api";
import { mockWorkflowsApi } from "./fixtures/workflows";
import { REVIEW_PR, WORKFLOW_DOCS } from "../src/workflows/fixture";
import type { WorkflowDef } from "../src/api/workflows";

/**
 * The Workflows tab's left pane: one row per workflow, laid out exactly as
 * the Rules board's rule list (same column, gap, button and row shapes).
 * Screenshots land where WORKFLOWS_LIST_SCREENSHOTS points (default
 * test-results/), next to one of the Rules board for comparison.
 */
const SHOTS = process.env.WORKFLOWS_LIST_SCREENSHOTS ?? "test-results";

/** A third workflow whose one step runs on spark2: its row's dot wears spark2's colour. */
const NIGHTLY: WorkflowDef = {
  id: "nightly-report",
  name: "Nightly report",
  version: 1,
  steps: [{ id: "collect", name: "Collect", kind: "logic", placement: { machine: "spark2" } }],
};

async function agentState(page: Page) {
  return JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}");
}

async function open(page: Page, start: WorkflowDef[], path = "/workflows?id=review-pr") {
  await mockApi(page);
  const calls = await mockWorkflowsApi(page, start);
  await page.goto(path);
  await expect.poll(async () => (await agentState(page)).status).toBe("ready");
  return calls;
}

const list = (page: Page, name: "Workflows" | "Rules") => page.getByRole("navigation", { name });
const box = async (l: Locator) => (await l.boundingBox())!;
const css = (l: Locator, prop: string) =>
  l.evaluate((el, p) => getComputedStyle(el).getPropertyValue(p), prop);

async function noSeriousAxe(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${label}: ${v.id} — ${v.help} (${v.nodes.map((n) => n.target).join(" ")})`)).toEqual([]);
}

test.describe("Workflows tab: the workflow list", () => {
  test("sits left of the board, aligned with the Rules list (screenshots)", async ({ page }) => {
    await open(page, [...WORKFLOW_DOCS, NIGHTLY]);
    const wf = list(page, "Workflows");
    await expect(wf.getByRole("link")).toHaveText(["Review PR", "Build image", "Nightly report"]);
    await expect(wf.getByRole("link", { name: "Review PR" })).toHaveAttribute("aria-current", "true");
    await expect(page.locator(".rule-row.is-selected")).toHaveAttribute("data-workflow-id", "review-pr");
    await expect(wf.getByRole("switch")).toHaveCount(3);
    // Dots: Nightly report runs only on spark2 (slot 2); the others span or defer machines.
    const dot = (id: string) => page.locator(`.rule-row[data-workflow-id="${id}"] .machine-dot`);
    await expect(dot("nightly-report")).toHaveAttribute("data-machine-slot", "2");
    await expect(dot("review-pr")).toHaveAttribute("data-machine-slot", "none");
    await expect(dot("build-image")).toHaveAttribute("data-machine-slot", "none");

    // Two columns: the list, then the board (head, canvas) to its right.
    const wfList = await box(wf);
    const board = await box(page.getByRole("main"));
    const canvas = await box(page.locator(".wf-canvas"));
    expect(wfList.x + wfList.width).toBeLessThanOrEqual(board.x);
    expect(Math.round(board.x - (wfList.x + wfList.width))).toBe(28);
    expect(canvas.x).toBeGreaterThanOrEqual(board.x);
    const newButton = wf.getByRole("button", { name: "New workflow" });
    const newBox = await box(newButton);
    const firstRow = await box(page.locator(".rule-row").first());
    const rowStyle = {
      padding: await css(page.locator(".rule-row").first(), "padding"),
      radius: await css(page.locator(".rule-row").first(), "border-radius"),
      name: await css(wf.getByRole("link").first(), "font-size"),
      button: await css(newButton, "font"),
      dash: await css(newButton, "border-top-style"),
    };
    await page.screenshot({ path: `${SHOTS}/workflows-list-3.png`, fullPage: true });

    // The Rules board, measured the same way: same column, button and rows.
    await page.goto("/rules/build-and-publish");
    await expect.poll(async () => (await agentState(page)).status).toBe("ready");
    const rules = list(page, "Rules");
    const rulesList = await box(rules);
    expect(wfList.x).toBe(rulesList.x);
    expect(wfList.y).toBe(rulesList.y);
    // Same flex column (1 1 260px, max 300px); its share of the free space differs by a fraction of a px.
    expect(wfList.width).toBeCloseTo(rulesList.width, 0);
    const rulesNew = rules.getByRole("button", { name: "When does this happen?" });
    const rulesNewBox = await box(rulesNew);
    expect(newBox.x).toBe(rulesNewBox.x);
    expect(newBox.y).toBe(rulesNewBox.y);
    // Same width; the height follows the label ("When does this happen?" wraps to two lines).
    expect(newBox.width).toBeCloseTo(rulesNewBox.width, 0);
    const ruleRow = page.locator(".rule-row").first();
    // Rows start the same distance under the button (margin + gap).
    const under = (row: { y: number }, button: { y: number; height: number }) =>
      Math.round(row.y - (button.y + button.height));
    expect(under(await box(ruleRow), rulesNewBox)).toBe(under(firstRow, newBox));
    expect({
      padding: await css(ruleRow, "padding"),
      radius: await css(ruleRow, "border-radius"),
      name: await css(rules.getByRole("link").first(), "font-size"),
      button: await css(rulesNew, "font"),
      dash: await css(rulesNew, "border-top-style"),
    }).toEqual(rowStyle);
    await page.screenshot({ path: `${SHOTS}/rules-board.png`, fullPage: true });
  });

  test("shows with one workflow (screenshot) and with none", async ({ page }) => {
    await open(page, [REVIEW_PR], "/workflows");
    const wf = list(page, "Workflows");
    await expect(wf.getByRole("link")).toHaveText(["Review PR"]);
    await expect(wf.getByRole("link", { name: "Review PR" })).toHaveAttribute("aria-current", "true");
    await expect(page.getByRole("combobox", { name: "Workflow" })).toHaveCount(0);
    await page.screenshot({ path: `${SHOTS}/workflows-list-1.png`, fullPage: true });

    await page.unrouteAll({ behavior: "ignoreErrors" });
    await open(page, [], "/workflows");
    await expect(list(page, "Workflows").getByRole("button", { name: "New workflow" })).toBeVisible();
    await expect(list(page, "Workflows").getByRole("link")).toHaveCount(0);
    await expect(page.getByRole("region", { name: "No workflows yet" })).toBeVisible();
  });

  test("a row opens its workflow; its switch enables / disables it", async ({ page }) => {
    const calls = await open(page, [...WORKFLOW_DOCS, NIGHTLY], "/workflows?id=review-pr&run=run-7");
    const wf = list(page, "Workflows");
    await wf.getByRole("link", { name: "Build image" }).click();
    await expect(page).toHaveURL(/\/workflows\?id=build-image$/);
    await expect(page.getByRole("heading", { level: 1, name: "Build image" })).toBeVisible();
    await expect(wf.getByRole("link", { name: "Build image" })).toHaveAttribute("aria-current", "true");
    await expect.poll(async () => (await agentState(page)).workflows.selected).toBe("build-image");

    const toggle = wf.getByRole("switch", { name: "Nightly report enabled" });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-checked", "false");
    await expect(page.locator('.rule-row[data-workflow-id="nightly-report"]')).toHaveClass(/is-disabled/);
    expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual([
      "/api/workflows/nightly-report/disable",
    ]);
    // The open workflow stays open.
    await expect(page.getByRole("heading", { level: 1, name: "Build image" })).toBeVisible();
  });

  test("keyboard: Tab reaches every row, Enter opens it, Escape still closes the name form", async ({ page }) => {
    await open(page, [...WORKFLOW_DOCS, NIGHTLY]);
    const wf = list(page, "Workflows");
    const newButton = wf.getByRole("button", { name: "New workflow" });
    await newButton.focus();
    for (const name of ["Review PR", "Build image", "Nightly report"]) {
      await page.keyboard.press("Tab");
      await expect(wf.getByRole("link", { name })).toBeFocused();
      await page.keyboard.press("Tab");
      await expect(wf.getByRole("switch", { name: `${name} enabled` })).toBeFocused();
    }
    await wf.getByRole("link", { name: "Nightly report" }).focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/workflows\?id=nightly-report$/);
    await expect(page.getByRole("heading", { level: 1, name: "Nightly report" })).toBeVisible();

    await newButton.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("form", { name: "New workflow" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("form", { name: "New workflow" })).toHaveCount(0);
    await expect(newButton).toBeFocused();
  });

  test("axe: no serious or critical violations on the board with the list", async ({ page }) => {
    await open(page, [...WORKFLOW_DOCS, NIGHTLY]);
    await noSeriousAxe(page, "board");
    await list(page, "Workflows").getByRole("switch", { name: "Build image enabled" }).click();
    await expect(page.locator('.rule-row[data-workflow-id="build-image"]')).toHaveClass(/is-disabled/);
    await noSeriousAxe(page, "a disabled row");
  });
});
