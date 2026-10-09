import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { mockApi } from "./fixtures/api";
import { withView } from "./fixtures/view";
import { FORM_RUN_OUTPUTS, GONE_WORKFLOW, mockWorkflowsApi } from "./fixtures/workflows";
import { WORKFLOW_DOCS } from "../src/workflows/fixture";
import type { WorkflowDef } from "../src/api/workflows";

/**
 * The Workflows tab's editing flows: the Run form and its outputs, the step
 * panel's timeout/retry, the inputs/outputs editors on the in/out nodes, and
 * the soft-deleted view with the admin-only purge.
 */
async function agentState(page: Page) {
  return JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}");
}

async function open(
  page: Page,
  { roles, gone = [], path = "/workflows?id=review-pr" }: { roles?: string[]; gone?: WorkflowDef[]; path?: string } = {},
) {
  // The canvas scenarios drive the Detailed (steps) view; a workflow opens in Simple by default.
  await withView(page);
  await mockApi(page, { roles });
  const calls = await mockWorkflowsApi(page, WORKFLOW_DOCS, gone);
  await page.goto(path);
  await expect.poll(async () => (await agentState(page)).status).toBe("ready");
  await expect(step(page, "Decide")).toBeVisible();
  return calls;
}

const step = (page: Page, name: string): Locator => page.getByRole("group", { name, exact: true });

/** `skip` leaves out selectors of a known product bug (none today). */
async function noSeriousAxe(page: Page, label: string, skip: string[] = []) {
  const builder = new AxeBuilder({ page });
  for (const selector of skip) builder.exclude(selector);
  const results = await builder.analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${label}: ${v.id} — ${v.help} (${v.nodes.map((n) => n.target).join(" ")})`)).toEqual([]);
}

test.describe("Workflows: Run form", () => {
  test("Run opens a typed form; submitting POSTs typed inputs, overlays the run and shows its outputs", async ({ page }) => {
    const calls = await open(page);
    await page.getByRole("button", { name: "Run", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Run Review PR" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel(/^pr/)).toBeFocused();
    await noSeriousAxe(page, "run form");

    // A required field is refused client-side before anything is sent.
    await dialog.getByRole("button", { name: "Run", exact: true }).click();
    await expect(dialog.getByText("Required").first()).toBeVisible();
    expect(calls.some((c) => c.path === "/api/workflows/review-pr/run")).toBe(false);
    await noSeriousAxe(page, "run form with errors");

    await dialog.getByLabel(/^pr/).fill("42");
    await dialog.getByLabel(/^repo/).fill("acme/app");
    await dialog.getByRole("button", { name: "Run", exact: true }).click();

    await expect(dialog).toHaveCount(0);
    const post = calls.find((c) => c.method === "POST" && c.path === "/api/workflows/review-pr/run");
    expect(post?.body).toEqual({ inputs: { pr: 42, repo: "acme/app" } });
    expect(typeof (post?.body as { inputs: { pr: unknown } }).inputs.pr).toBe("number");

    // The run is overlaid on the canvas, and its outputs render once it finishes.
    await expect(page).toHaveURL(/run=run-9/);
    const outputs = page.getByRole("region", { name: "Run outputs" });
    await expect(outputs).toBeVisible({ timeout: 10_000 });
    await expect(outputs).toContainText("verdict");
    await expect(outputs).toContainText(FORM_RUN_OUTPUTS.verdict);
    await expect(outputs).toContainText(FORM_RUN_OUTPUTS.owner);
    await noSeriousAxe(page, "run outputs");
  });

  test("Escape closes the form without sending", async ({ page }) => {
    const calls = await open(page);
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Run Review PR" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Run Review PR" })).toHaveCount(0);
    expect(calls.some((c) => c.path.endsWith("/run"))).toBe(false);
  });
});

test.describe("Workflows: step panel", () => {
  test("an invalid timeout is a guided error that blocks Done; a valid one is saved", async ({ page }) => {
    const calls = await open(page);
    await step(page, "Review").locator(".wf-card__name").click();
    await page.getByRole("button", { name: "Edit Review" }).click();
    const dialog = page.getByRole("dialog", { name: "Edit Review" });
    await expect(dialog).toBeVisible();
    await noSeriousAxe(page, "step panel");

    const timeout = dialog.getByLabel("Timeout (seconds)");
    if (!(await timeout.isVisible())) await dialog.getByText("Timeout and retries").click();
    await timeout.fill("0");
    await expect(dialog.getByRole("alert")).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Done" })).toBeDisabled();
    await noSeriousAxe(page, "step panel with an invalid timeout");

    await timeout.fill("45");
    await dialog.getByLabel("Max attempts").fill("3");
    await expect(dialog.getByRole("alert")).toHaveCount(0);
    await expect(dialog.getByRole("button", { name: "Done" })).toBeEnabled();
    await dialog.getByRole("button", { name: "Done" }).click();
    await expect(dialog).toHaveCount(0);

    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => calls.filter((c) => c.method === "PUT").length).toBe(1);
    const body = calls.find((c) => c.method === "PUT")!.body as {
      steps: { id: string; timeout_s?: number; retry?: { max_attempts?: number } }[];
    };
    const review = body.steps.find((s) => s.id === "review")!;
    expect(review.timeout_s).toBe(45);
    expect(review.retry?.max_attempts).toBe(3);
  });
});

test.describe("Workflows: inputs and outputs editors", () => {
  test("the in node opens the inputs editor on click and Enter; an input can be added and saved", async ({ page }) => {
    const calls = await open(page);
    await step(page, "Inputs").locator(".wf-card__io-title").click();
    const dialog = page.getByRole("dialog", { name: "Edit inputs" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("textbox", { name: "Name of input pr" })).toHaveValue("pr");
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);

    await step(page, "Inputs").focus();
    await page.keyboard.press("Enter");
    await expect(dialog).toBeVisible();
    await noSeriousAxe(page, "inputs editor");

    await dialog.getByRole("button", { name: "Add input" }).click();
    const name = dialog.getByRole("textbox", { name: "Name of input input1" });
    await name.fill("branch");
    await dialog.getByRole("combobox", { name: "Type of input branch" }).selectOption("string");
    await expect(page.getByTestId("rf__node-inputs")).toContainText("branch");
    await noSeriousAxe(page, "inputs editor with a new input");
    await page.keyboard.press("Escape");

    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => calls.filter((c) => c.method === "PUT").length).toBe(1);
    const body = calls.find((c) => c.method === "PUT")!.body as { inputs: { name: string; type: string }[] };
    expect(body.inputs.map((p) => p.name)).toEqual(["pr", "repo", "branch"]);
    expect(body.inputs.at(-1)?.type).toBe("string");
  });

  test("the out node opens the outputs and variables editor", async ({ page }) => {
    await open(page);
    await step(page, "Outputs").locator(".wf-card__io-title").click();
    const dialog = page.getByRole("dialog", { name: "Edit outputs" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("group", { name: "Variables" })).toBeVisible();
    await expect(dialog.getByRole("textbox", { name: "Name of output verdict" })).toHaveValue("verdict");
    await noSeriousAxe(page, "outputs editor");
  });
});

test.describe("Workflows: deleted and purge", () => {
  const showDeleted = async (page: Page) => {
    await page.getByRole("button", { name: "Show deleted" }).click();
    await expect(page.getByRole("button", { name: "Restore Old flow" })).toBeVisible();
  };

  test("an admin sees Purge; it dry-runs, then Confirm purge sends {apply:true}", async ({ page }) => {
    const calls = await open(page, { gone: [GONE_WORKFLOW] });
    await expect(page.getByText("Old flow")).toHaveCount(0);
    await showDeleted(page);
    expect(calls.some((c) => c.path === "/api/workflows" && c.search.includes("include_deleted=true"))).toBe(true);
    await noSeriousAxe(page, "deleted view");

    await page.getByRole("button", { name: "Purge Old flow" }).click();
    const panel = page.getByRole("region", { name: "Purge Old flow" });
    const confirm = panel.getByRole("button", { name: "Confirm purge" });
    await expect(confirm).toBeEnabled();
    await noSeriousAxe(page, "purge confirmation");
    const purges = () => calls.filter((c) => c.path === "/api/workflows/old-flow/purge").map((c) => c.body);
    expect(purges()).toEqual([{ apply: false }]);

    await confirm.click();
    await expect(panel).toHaveCount(0);
    expect(purges()).toEqual([{ apply: false }, { apply: true }]);
    await expect(page.getByText("Purged Old flow")).toBeVisible();
    await expect(page.getByRole("button", { name: "Restore Old flow" })).toHaveCount(0);
  });

  test("Cancel leaves the workflow unpurged", async ({ page }) => {
    const calls = await open(page, { gone: [GONE_WORKFLOW] });
    await showDeleted(page);
    await page.getByRole("button", { name: "Purge Old flow" }).click();
    await page.getByRole("region", { name: "Purge Old flow" }).getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("region", { name: "Purge Old flow" })).toHaveCount(0);
    expect(calls.filter((c) => c.path.endsWith("/purge")).map((c) => c.body)).toEqual([{ apply: false }]);
  });

  test("a non-admin sees the deleted workflow and Restore, but no Purge", async ({ page }) => {
    await open(page, { roles: ["viewer", "editor"], gone: [GONE_WORKFLOW] });
    await showDeleted(page);
    await expect(page.getByRole("button", { name: /^Purge/ })).toHaveCount(0);
    await noSeriousAxe(page, "deleted view, non-admin");
  });
});
