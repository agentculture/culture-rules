import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { mockApi } from "./fixtures/api";
import { withView } from "./fixtures/view";
import { mockWorkflowsApi } from "./fixtures/workflows";
import { REVIEW_PR, WORKFLOW_DOCS } from "../src/workflows/fixture";
import type { WorkflowDef } from "../src/api/workflows";

/**
 * The Workflows tab's left pane, folded (t7, t8): chain cards, one section
 * per workflow (its machine dot, its name linking to it, its (i) and its
 * enable switch) with what starts it, what it continues into, how it runs and
 * ends; then the rules without a workflow. The Rules list it used to mirror is
 * gone. Screenshots land where WORKFLOWS_LIST_SCREENSHOTS points (default
 * test-results/).
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
  // The canvas scenarios drive the Detailed (steps) view; a workflow opens in Simple by default.
  await withView(page);
  await mockApi(page);
  const calls = await mockWorkflowsApi(page, start);
  await page.goto(path);
  await expect.poll(async () => (await agentState(page)).status).toBe("ready");
  return calls;
}

const list = (page: Page) => page.getByRole("navigation", { name: "Workflows" });
const section = (page: Page, id: string) => page.locator(`.fold-workflow[data-workflow-id="${id}"]`);
/** The workflow name links, in list order (entry-point and D7 links are not workflow links). */
const workflowLinks = (page: Page) => list(page).locator(".fold-workflow h3").getByRole("link");
const box = async (l: Locator) => (await l.boundingBox())!;

async function noSeriousAxe(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${label}: ${v.id} — ${v.help} (${v.nodes.map((n) => n.target).join(" ")})`)).toEqual([]);
}

test.describe("Workflows tab: the workflow list", () => {
  test("sits left of the board: one section per workflow with its dot, switch and selection (screenshots)", async ({ page }) => {
    await open(page, [...WORKFLOW_DOCS, NIGHTLY]);
    const wf = list(page);
    await expect(workflowLinks(page)).toHaveText(["Review PR", "Build image", "Nightly report"]);
    await expect(workflowLinks(page).filter({ hasText: "Review PR" })).toHaveAttribute("aria-current", "true");
    await expect(wf.locator("a[aria-current]")).toHaveCount(1);
    await expect(wf.getByRole("switch")).toHaveCount(3);
    // Dots: Nightly report runs only on spark2 (slot 2); the others span or defer machines.
    const dot = (id: string) => section(page, id).locator(".machine-dot");
    await expect(dot("nightly-report")).toHaveAttribute("data-machine-slot", "2");
    await expect(dot("review-pr")).toHaveAttribute("data-machine-slot", "none");
    await expect(dot("build-image")).toHaveAttribute("data-machine-slot", "none");
    // The rules that start each workflow are named on it, linking to the entry point.
    await expect(section(page, "build-image").getByRole("link", { name: "Build and publish" })).toHaveAttribute(
      "href",
      "/workflows?id=build-image&entry=build-and-publish",
    );

    // Two columns: the list, then the board (head, canvas) to its right, 28px apart (the board's gap).
    const wfList = await box(wf);
    const board = await box(page.getByRole("main"));
    const canvas = await box(page.locator(".wf-canvas"));
    expect(wfList.x + wfList.width).toBeLessThanOrEqual(board.x);
    expect(Math.round(board.x - (wfList.x + wfList.width))).toBe(28);
    expect(canvas.x).toBeGreaterThanOrEqual(board.x);
    // New workflow is a large target on top of the list.
    const newBox = await box(wf.getByRole("button", { name: "New workflow" }));
    expect(newBox.height).toBeGreaterThanOrEqual(44);
    expect(newBox.y).toBeLessThanOrEqual((await box(workflowLinks(page).first())).y);
    await page.screenshot({ path: `${SHOTS}/workflows-list-3.png`, fullPage: true });
  });

  test("shows with one workflow (screenshot) and with none", async ({ page }) => {
    await open(page, [REVIEW_PR], "/workflows");
    // Build and publish still names build-image, which is not stored: listed, and said to be missing.
    await expect(workflowLinks(page)).toHaveText(["Review PR", "build-image"]);
    await expect(section(page, "build-image")).toContainText("Workflow definition missing");
    await expect(section(page, "build-image").getByRole("switch")).toHaveCount(0);
    await expect(workflowLinks(page).first()).toHaveAttribute("aria-current", "true");
    await expect(page.getByRole("combobox", { name: "Workflow" })).toHaveCount(0);
    await page.screenshot({ path: `${SHOTS}/workflows-list-1.png`, fullPage: true });

    await page.unrouteAll({ behavior: "ignoreErrors" });
    await open(page, [], "/workflows");
    await expect(list(page).getByRole("button", { name: "New workflow" })).toBeVisible();
    // No stored workflow: no switch. The fixture's two rules still name theirs, shown as missing.
    await expect(list(page).getByRole("switch")).toHaveCount(0);
    await expect(list(page).getByText("Workflow definition missing")).toHaveCount(2);
    await expect(page.getByRole("region", { name: "No workflows yet" })).toBeVisible();
  });

  test("a workflow's link opens it; its switch enables / disables it", async ({ page }) => {
    const calls = await open(page, [...WORKFLOW_DOCS, NIGHTLY], "/workflows?id=review-pr&run=run-7");
    const wf = list(page);
    await workflowLinks(page).filter({ hasText: "Build image" }).click();
    await expect(page).toHaveURL(/\/workflows\?id=build-image$/);
    await expect(page.getByRole("heading", { level: 1, name: "Build image" })).toBeVisible();
    await expect(workflowLinks(page).filter({ hasText: "Build image" })).toHaveAttribute("aria-current", "true");
    await expect.poll(async () => (await agentState(page)).workflows.selected).toBe("build-image");

    const toggle = wf.getByRole("switch", { name: "Nightly report enabled" });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-checked", "false");
    await expect(section(page, "nightly-report")).toHaveClass(/is-disabled/);
    expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual([
      "/api/workflows/nightly-report/disable",
    ]);
    // The open workflow stays open.
    await expect(page.getByRole("heading", { level: 1, name: "Build image" })).toBeVisible();
  });

  test("keyboard: Tab reaches every workflow's link, (i) and switch; Enter opens it; Escape still closes the name form", async ({ page }) => {
    await open(page, [...WORKFLOW_DOCS, NIGHTLY]);
    const wf = list(page);
    const newButton = wf.getByRole("button", { name: "New workflow" });
    await newButton.focus();
    /** Tab until `target` holds focus (within a bound: every stop is a real control). */
    const tabTo = async (target: Locator) => {
      for (let i = 0; i < 40 && !(await target.evaluate((el) => el === document.activeElement)); i++) {
        await page.keyboard.press("Tab");
      }
      await expect(target).toBeFocused();
    };
    for (const [id, name] of [["review-pr", "Review PR"], ["build-image", "Build image"], ["nightly-report", "Nightly report"]]) {
      await tabTo(section(page, id).locator("h3").getByRole("link", { name }));
      await page.keyboard.press("Tab");
      await expect(wf.getByRole("button", { name: `About ${name}` })).toBeFocused();
      await page.keyboard.press("Tab");
      await expect(wf.getByRole("switch", { name: `${name} enabled` })).toBeFocused();
    }
    await workflowLinks(page).filter({ hasText: "Nightly report" }).focus();
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
    await list(page).getByRole("switch", { name: "Build image enabled" }).click();
    await expect(section(page, "build-image")).toHaveClass(/is-disabled/);
    await noSeriousAxe(page, "a disabled workflow");
  });
});
