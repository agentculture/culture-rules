import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./fixtures/api";
import { mockWorkflowsApi, type Recorded } from "./fixtures/workflows";
import { WORKFLOW_DOCS } from "../src/workflows/fixture";
import type { WorkflowDef } from "../src/api/workflows";

/**
 * The Workflows tab's "New workflow": the button atop the workflow list and
 * the empty state's primary action, a one-question name form, the created
 * workflow opened on the canvas; plus the list row's enable switch and
 * delete with Undo. Screenshots land where WORKFLOWS_CREATE_SCREENSHOTS
 * points (default test-results/).
 */
const SHOTS = process.env.WORKFLOWS_CREATE_SCREENSHOTS ?? "test-results";

/** A workflow no rule uses, so it can be deleted. */
const SPARE: WorkflowDef = { id: "nightly-report", name: "Nightly report", version: 1, steps: [] };

async function agentState(page: Page) {
  return JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}");
}

async function open(page: Page, start: WorkflowDef[], path = "/workflows"): Promise<Recorded[]> {
  await mockApi(page);
  const calls = await mockWorkflowsApi(page, start);
  await page.goto(path);
  await expect.poll(async () => (await agentState(page)).status).toBe("ready");
  return calls;
}

const posts = (calls: Recorded[]) =>
  calls.filter((c) => c.method === "POST" && c.path === "/api/workflows");
const emptyState = (page: Page) => page.getByRole("region", { name: "No workflows yet" });
const nameForm = (page: Page) => page.getByRole("form", { name: "New workflow" });
const list = (page: Page) => page.getByRole("navigation", { name: "Workflows" });
const listNew = (page: Page) => list(page).getByRole("button", { name: "New workflow" });

async function noSeriousAxe(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${label}: ${v.id} — ${v.help} (${v.nodes.map((n) => n.target).join(" ")})`)).toEqual([]);
}

test.describe("Workflows tab: New workflow", () => {
  test("empty state: the primary action asks for a name, Enter creates and opens the canvas", async ({ page }) => {
    const calls = await open(page, []);
    await expect(page.getByRole("heading", { level: 1, name: "No workflows yet" })).toBeVisible();
    const cta = emptyState(page).getByRole("button", { name: "New workflow" });
    await expect(cta).toBeVisible();
    // Large target: the primary action is drawn at 56px.
    expect(Math.round((await cta.boundingBox())!.height)).toBe(56);
    // The list is there with no workflows: its New button, no rows; the header has no "+ New".
    await expect(listNew(page)).toBeVisible();
    await expect(list(page).getByRole("link")).toHaveCount(0);
    const head = page.locator(".wf-head__end");
    await expect(head.getByRole("button", { name: "New workflow" })).toHaveCount(0);
    await expect(head.getByRole("button", { name: "Import", exact: true })).toBeVisible();
    expect((await agentState(page)).workflows).toMatchObject({ count: 0, selected: null });
    await noSeriousAxe(page, "empty state");
    await page.screenshot({ path: `${SHOTS}/workflows-empty.png`, fullPage: true });

    await cta.click();
    const name = nameForm(page).getByRole("textbox", { name: "Name" });
    await expect(name).toBeFocused();
    await expect(nameForm(page).getByRole("textbox")).toHaveCount(1);
    await name.fill("Triage issues");
    await noSeriousAxe(page, "name form");
    await page.screenshot({ path: `${SHOTS}/workflows-new-form.png`, fullPage: true });

    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { level: 1, name: "Triage issues" })).toBeVisible();
    expect(posts(calls).map((c) => c.body)).toEqual([
      {
        id: "triage-issues",
        name: "Triage issues",
        inputs: [],
        variables: [],
        steps: [],
        edges: [],
        outputs: [],
        enabled: true,
      },
    ]);
    await expect(page).toHaveURL(/\/workflows\?id=triage-issues$/);
    await expect(page.getByRole("group", { name: "Inputs", exact: true })).toBeVisible();
    // Straight into editing: the step + has focus; Enter adds the first step.
    const add = page.getByRole("button", { name: "Add step" });
    await expect(add).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("group", { name: "New step", exact: true })).toBeVisible();
    await expect
      .poll(async () => (await agentState(page)).workflows)
      .toMatchObject({ count: 1, selected: "triage-issues", steps: ["step-1"], dirty: true });
    expect((await agentState(page)).status).toBe("ready");
  });

  test("Escape closes the name form without writing", async ({ page }) => {
    const calls = await open(page, []);
    await emptyState(page).getByRole("button", { name: "New workflow" }).click();
    await expect(nameForm(page)).toBeVisible();
    await page.keyboard.type("Draft");
    await page.keyboard.press("Escape");
    await expect(nameForm(page)).toHaveCount(0);
    await expect(emptyState(page)).toBeVisible();
    await expect(listNew(page)).toBeFocused();
    expect(calls.filter((c) => c.method !== "GET")).toEqual([]);
  });

  test("with a workflow selected, the list's New button creates and switches to the new one", async ({ page }) => {
    const calls = await open(page, WORKFLOW_DOCS, "/workflows?id=review-pr");
    await expect(page.getByRole("heading", { level: 1, name: "Review PR" })).toBeVisible();
    // The design board's head stays one row at 1280px beside the list: title, rename, delete, io group, Run.
    const middle = async (name: string, role: "heading" | "button" | "switch") => {
      const box = (await page.getByRole(role, { name, exact: true }).first().boundingBox())!;
      return box.y + box.height / 2;
    };
    const row = await middle("Review PR", "heading");
    for (const [name, role] of [
      ["Rename workflow", "button"],
      ["Delete workflow", "button"],
      ["Import", "button"],
      ["Export", "button"],
      ["Run", "button"],
    ] as const) {
      expect(Math.abs((await middle(name, role)) - row)).toBeLessThan(12);
    }
    // The enable switch lives on the list row (as on Rules), not in the head.
    await expect(page.locator(".wf-head").getByRole("switch")).toHaveCount(0);
    await listNew(page).click();
    await expect(page.getByRole("heading", { level: 1, name: "New workflow" })).toBeVisible();
    await nameForm(page).getByRole("textbox", { name: "Name" }).fill("Ship release");
    await nameForm(page).getByRole("button", { name: "Create workflow" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Ship release" })).toBeVisible();
    await expect(page).toHaveURL(/\/workflows\?id=ship-release$/);
    await expect(list(page).getByRole("link", { name: "Ship release" })).toHaveAttribute("aria-current", "true");
    await expect(list(page).getByRole("link")).toHaveCount(3);
    expect(posts(calls)).toHaveLength(1);
  });

  test("an API error shows inline in the form", async ({ page }) => {
    await mockApi(page);
    await mockWorkflowsApi(page, []);
    await page.route("**/api/workflows", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      await route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          error: { code: "invalid_workflow", message: "name: must be at most 80 characters", errors: [] },
        }),
      });
    });
    await page.goto("/workflows");
    await expect.poll(async () => (await agentState(page)).status).toBe("ready");
    await emptyState(page).getByRole("button", { name: "New workflow" }).click();
    await nameForm(page).getByRole("textbox", { name: "Name" }).fill("Too long");
    await page.keyboard.press("Enter");
    await expect(nameForm(page).getByRole("alert")).toHaveText("name: must be at most 80 characters");
    await expect(nameForm(page).getByRole("textbox", { name: "Name" })).toHaveValue("Too long");
  });
});

test.describe("Workflows tab: rename, enable / disable and delete", () => {
  test("rename edits the draft name; Save PUTs it", async ({ page }) => {
    const calls = await open(page, WORKFLOW_DOCS, "/workflows?id=build-image");
    await page.getByRole("button", { name: "Rename workflow" }).click();
    const field = page.getByRole("form", { name: "Rename workflow" }).getByRole("textbox", { name: "Name" });
    await expect(field).toBeFocused();
    await expect(field).toHaveValue("Build image");
    await field.fill("Build and push image");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { level: 1, name: "Build and push image" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Rename workflow" })).toBeFocused();
    await page.getByRole("button", { name: "Save" }).click();
    await expect.poll(() => calls.filter((c) => c.method === "PUT").length).toBe(1);
    expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({
      id: "build-image",
      name: "Build and push image",
    });
  });

  test("the list row's switch disables and enables the stored workflow", async ({ page }) => {
    const calls = await open(page, [...WORKFLOW_DOCS, SPARE], "/workflows?id=nightly-report");
    const toggle = list(page).getByRole("switch", { name: "Nightly report enabled" });
    await expect(toggle).toHaveAttribute("aria-checked", "true");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-checked", "false");
    await expect(page.locator('.rule-row[data-workflow-id="nightly-report"]')).toHaveClass(/is-disabled/);
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual([
      "/api/workflows/nightly-report/disable",
      "/api/workflows/nightly-report/enable",
    ]);
  });

  test("delete is soft, with Undo; a workflow a rule uses is kept and the conflict named", async ({ page }) => {
    const calls = await open(page, [...WORKFLOW_DOCS, SPARE], "/workflows?id=nightly-report");
    await page.getByRole("button", { name: "Delete workflow" }).click();
    await expect(page.getByText("Deleted Nightly report")).toBeVisible();
    await expect(page.getByRole("heading", { level: 1, name: "Build image" })).toBeVisible();
    await page.getByRole("button", { name: "Undo" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Nightly report" })).toBeVisible();
    expect(calls.filter((c) => c.method !== "GET").map((c) => `${c.method} ${c.path}`)).toEqual([
      "DELETE /api/workflows/nightly-report",
      "POST /api/workflows/nightly-report/restore",
    ]);

    await list(page).getByRole("link", { name: "Review PR" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Review PR" })).toBeVisible();
    await page.getByRole("button", { name: "Delete workflow" }).click();
    await expect(page.getByRole("alert")).toContainText("workflows/review-pr is used by a rule");
    await expect(page.getByRole("heading", { level: 1, name: "Review PR" })).toBeVisible();
  });
});
