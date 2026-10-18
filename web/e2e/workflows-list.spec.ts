import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { mockApi } from "./fixtures/api";
import { withView } from "./fixtures/view";
import { mockWorkflowsApi } from "./fixtures/workflows";
import { REVIEW_PR, WORKFLOW_DOCS } from "../src/workflows/fixture";
import type { WorkflowDef } from "../src/api/workflows";

/**
 * The Workflows tab's compact pane and its overview (d13): the pane is names
 * only; the overview is one card per workflow across the full width, with no
 * chain or housekeeping section (restore / purge are CLI / MCP only; the
 * enable switch is in the open workflow's head). Screenshots land where WORKFLOWS_LIST_SCREENSHOTS points (default
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

/** The compact pane while a workflow is open (d6): a search box and workflow names only. */
const pane = (page: Page) => page.locator("aside.wf-pane");
const paneLinks = (page: Page) => pane(page).getByRole("navigation", { name: "Workflows" }).getByRole("link");
const box = async (l: Locator) => (await l.boundingBox())!;

async function noSeriousAxe(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${label}: ${v.id} — ${v.help} (${v.nodes.map((n) => n.target).join(" ")})`)).toEqual([]);
}

test.describe("Workflows tab: the workflow list", () => {
  test("an open workflow shows a compact pane left of the board: names only, the open one current (screenshot)", async ({ page }) => {
    await open(page, [...WORKFLOW_DOCS, NIGHTLY]);
    await expect(paneLinks(page)).toHaveText(["Review PR", "Build image", "Nightly report"]);
    await expect(paneLinks(page).filter({ hasText: "Review PR" })).toHaveAttribute("aria-current", "page");
    await expect(pane(page).locator("a[aria-current]")).toHaveCount(1);
    // Names only: no chain cards, switches or dots in the pane (they live in the overview).
    await expect(pane(page).getByRole("switch")).toHaveCount(0);
    await expect(pane(page).getByRole("searchbox", { name: "Search workflows" })).toBeVisible();

    // Two columns: the pane, then the board (head, canvas) to its right, 28px apart (the board's gap).
    const side = await box(pane(page));
    const board = await box(page.getByRole("main"));
    const canvas = await box(page.locator(".wf-canvas"));
    expect(side.x + side.width).toBeLessThanOrEqual(board.x);
    expect(Math.round(board.x - (side.x + side.width))).toBe(28);
    expect(canvas.x).toBeGreaterThanOrEqual(board.x);
    // New workflow is a large target on top of the pane.
    const newBox = await box(pane(page).getByRole("button", { name: "New workflow" }));
    expect(newBox.height).toBeGreaterThanOrEqual(44);
    expect(newBox.y).toBeLessThanOrEqual((await box(paneLinks(page).first())).y);
    await page.screenshot({ path: `${SHOTS}/workflows-list-3.png`, fullPage: true });
  });

  test("the search box filters the pane's names; Escape clears it", async ({ page }) => {
    await open(page, [...WORKFLOW_DOCS, NIGHTLY]);
    const search = pane(page).getByRole("searchbox", { name: "Search workflows" });
    await search.fill("night");
    await expect(paneLinks(page)).toHaveText(["Nightly report"]);
    await search.fill("zzz");
    await expect(pane(page).getByText("No match")).toBeVisible();
    await search.press("Escape");
    await expect(search).toHaveValue("");
    await expect(paneLinks(page)).toHaveCount(3);
  });

  test("the overview is one card per workflow across the full main width (screenshot)", async ({ page }) => {
    await open(page, [...WORKFLOW_DOCS, NIGHTLY], "/workflows");
    const cards = page.getByRole("list", { name: "All workflows" }).getByRole("listitem");
    await expect(cards).toHaveCount(3);
    await expect(cards.getByRole("link")).toHaveText([/Review PR/, /Build image/, /Nightly report/]);
    // Nothing is open: no pane, no selection, and no chains / housekeeping section (d13).
    await expect(pane(page)).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Chains and housekeeping" })).toHaveCount(0);
    await expect(page.getByText("Rules without a workflow")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Show deleted" })).toHaveCount(0);
    // The cards fill the row: the last card ends where the main column ends.
    const main = await box(page.getByRole("region", { name: "Workflows overview" }));
    const last = await box(cards.last());
    expect(Math.round(main.x + main.width - (last.x + last.width))).toBeLessThanOrEqual(1);
    await page.screenshot({ path: `${SHOTS}/workflows-list-overview.png`, fullPage: true });
  });

  test("shows with one workflow (screenshot) and with none", async ({ page }) => {
    await open(page, [REVIEW_PR], "/workflows");
    await expect(page.getByRole("list", { name: "All workflows" }).getByRole("listitem")).toHaveCount(1);
    await expect(page.getByRole("combobox", { name: "Workflow" })).toHaveCount(0);
    await page.screenshot({ path: `${SHOTS}/workflows-list-1.png`, fullPage: true });

    await page.unrouteAll({ behavior: "ignoreErrors" });
    await open(page, [], "/workflows");
    await expect(page.getByRole("region", { name: "No workflows yet" }).getByRole("button", { name: "New workflow" })).toBeVisible();
    await expect(page.getByRole("switch")).toHaveCount(0);
  });

  test("a workflow's link opens it; its switch (in the head) enables / disables it", async ({ page }) => {
    const calls = await open(page, [...WORKFLOW_DOCS, NIGHTLY], "/workflows?id=review-pr&run=run-7");
    await paneLinks(page).filter({ hasText: "Build image" }).click();
    await expect(page).toHaveURL(/\/workflows\?id=build-image$/);
    await expect(page.getByRole("heading", { level: 1, name: "Build image" })).toBeVisible();
    await expect(paneLinks(page).filter({ hasText: "Build image" })).toHaveAttribute("aria-current", "page");
    await expect.poll(async () => (await agentState(page)).workflows.selected).toBe("build-image");

    // The enable switch lives in the open workflow's head (it was on the chain cards).
    await paneLinks(page).filter({ hasText: "Nightly report" }).click();
    const toggle = page.locator(".wf-head").getByRole("switch", { name: "Nightly report enabled" });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-checked", "false");
    await expect(paneLinks(page).filter({ hasText: "Nightly report" })).toHaveClass(/is-disabled/);
    expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual([
      "/api/workflows/nightly-report/disable",
    ]);
    // The overview's card links open a workflow again.
    await page.getByRole("link", { name: "← All workflows" }).click();
    await page.getByRole("link", { name: /^Build image/ }).first().click();
    await expect(page.getByRole("heading", { level: 1, name: "Build image" })).toBeVisible();
  });

  test("keyboard: Tab reaches every card, Enter opens it; Escape still closes the name form", async ({ page }) => {
    await open(page, [...WORKFLOW_DOCS, NIGHTLY], "/workflows");
    const cards = page.getByRole("list", { name: "All workflows" }).getByRole("link");
    await cards.first().focus();
    for (const name of [/Review PR/, /Build image/, /Nightly report/]) {
      await expect(cards.filter({ hasText: name })).toBeFocused();
      await page.keyboard.press("Tab");
    }
    await cards.filter({ hasText: /Nightly report/ }).focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/workflows\?id=nightly-report$/);
    await expect(page.getByRole("heading", { level: 1, name: "Nightly report" })).toBeVisible();

    const paneNew = pane(page).getByRole("button", { name: "New workflow" });
    await paneNew.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("form", { name: "New workflow" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("form", { name: "New workflow" })).toHaveCount(0);
    await expect(paneNew).toBeFocused();
  });

  test("axe: no serious or critical violations on the board with the pane, and on the overview", async ({ page }) => {
    await open(page, [...WORKFLOW_DOCS, NIGHTLY]);
    await noSeriousAxe(page, "board");
    await page.goto("/workflows");
    await expect(page.getByRole("region", { name: "Workflows overview" })).toBeVisible();
    await noSeriousAxe(page, "overview");
    await page.goto("/workflows?id=build-image");
    await page.locator(".wf-head").getByRole("switch", { name: "Build image enabled" }).click();
    await expect(paneLinks(page).filter({ hasText: "Build image" })).toHaveClass(/is-disabled/);
    await noSeriousAxe(page, "a disabled workflow");
  });
});
