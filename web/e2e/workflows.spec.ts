import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { mockApi } from "./fixtures/api";
import { mockWorkflowsApi } from "./fixtures/workflows";
import { IMPORT_FILE_NAME, IMPORT_FILE_TEXT, REVIEW_PR_DESCRIBED } from "../src/workflows/fixture";

/** Where the Workflows screenshot lands, for review against the 'Chosen — Workflows' board. */
const SCREENSHOT = process.env.WORKFLOWS_SCREENSHOT ?? "test-results/workflows.png";

async function agentState(page: Page) {
  return JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}");
}

async function open(page: Page, path = "/workflows?id=review-pr") {
  await mockApi(page);
  const calls = await mockWorkflowsApi(page);
  await page.goto(path);
  await expect.poll(async () => (await agentState(page)).status).toBe("ready");
  await expect(step(page, "Decide")).toBeVisible();
  return calls;
}

const step = (page: Page, name: string): Locator =>
  page.getByRole("group", { name, exact: true });
const edge = (page: Page, id: string) => page.getByTestId(`rf__edge-${id}`);
const dash = (locator: Locator) =>
  locator.locator(".react-flow__edge-path").evaluate((el) => getComputedStyle(el).strokeDasharray);

test.describe("Workflows tab", () => {
  test("matches the 'Chosen — Workflows' board layout (screenshot)", async ({ page }) => {
    await open(page);
    // Head: Fraunces 40px title, version chip, Import / Export / repo picker / Run.
    const h1 = page.getByRole("heading", { level: 1, name: "Review PR" });
    expect(await h1.evaluate((el) => getComputedStyle(el).fontSize)).toBe("40px");
    expect(await h1.evaluate((el) => getComputedStyle(el).fontFamily)).toContain("Fraunces");
    await expect(page.getByText("v3", { exact: true })).toBeVisible();
    for (const name of ["Import", "Export", "Run"]) {
      await expect(page.getByRole("button", { name, exact: true })).toBeVisible();
    }
    await expect(page.getByRole("button", { name: /agentculture\/workflows/ })).toBeVisible();

    // Columns left to right: in | Fetch diff, Run tests | Review | Decide | out.
    const x = async (name: string) => (await step(page, name).boundingBox())!.x;
    const order = ["Inputs", "Fetch diff", "Review", "Decide", "Outputs"];
    const xs = await Promise.all(order.map(x));
    expect([...xs].sort((a, b) => a - b)).toEqual(xs);
    expect(Math.abs((await x("Run tests")) - (await x("Fetch diff")))).toBeLessThan(4);

    // Stage shapes: 190px cards; Decide (logic) dashed; in/out dashed with no shadow.
    const box = (await step(page, "Review").boundingBox())!;
    expect(Math.round(box.width)).toBe(190);
    const decide = step(page, "Decide").locator(".wf-card");
    expect(await decide.evaluate((el) => getComputedStyle(el).borderTopStyle)).toBe("dashed");
    expect(
      await step(page, "Inputs").locator(".wf-card").evaluate((el) => getComputedStyle(el).borderTopStyle),
    ).toBe("dashed");

    // Machine colors on the card headers: spark teal, thor amber, spark2 blue.
    const head = (name: string) =>
      step(page, name).locator(".wf-card__machine").evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(await head("Fetch diff")).toBe("rgb(227, 241, 238)");
    expect(await head("Review")).toBe("rgb(251, 234, 223)");
    expect(await head("Run tests")).toBe("rgb(230, 233, 248)");

    // Type scale: step names 17px bold, ports 14px mono.
    const name = step(page, "Review").locator(".wf-card__name");
    expect(await name.evaluate((el) => getComputedStyle(el).fontSize)).toBe("17px");
    const port = step(page, "Review").locator('[data-port="in:diff"]');
    expect(await port.evaluate((el) => getComputedStyle(el).fontSize)).toBe("14px");

    // Dashed edges mark cross-machine hops; same-machine edges are solid.
    await expect(page.locator(".react-flow__edge")).toHaveCount(8);
    expect(await dash(edge(page, "fetch-diff.diff->review.diff"))).toBe("7px, 6px");
    expect(await dash(edge(page, "review.owner->outputs.owner"))).toBe("7px, 6px");
    expect(await dash(edge(page, "decide.verdict->outputs.verdict"))).toBe("none");
    expect(await dash(edge(page, "inputs.repo->run-tests.repo"))).toBe("none");
    await expect(page.getByText("crosses machines")).toBeVisible();
    await expect(page.getByRole("button", { name: "Add step" })).toBeVisible();

    // The board shows Review selected, its toolbar floating above it.
    await step(page, "Review").locator(".wf-card__name").click();
    await expect(page.getByRole("button", { name: "on thor" })).toBeVisible();
    await page.mouse.move(0, 0);
    // The selection ring is a 3px ink outline once its transition settles.
    await expect
      .poll(() => step(page, "Review").locator(".wf-card").evaluate((el) => getComputedStyle(el).boxShadow))
      .toContain("0px 0px 0px 3px");
    await page.screenshot({ path: SCREENSHOT, fullPage: true });
  });

  test("edits a step's placement, enable switch and saves with PUT", async ({ page }) => {
    const calls = await open(page);
    await step(page, "Review").locator(".wf-card__name").click();
    await page.getByRole("button", { name: "on thor" }).click();
    const dialog = page.getByRole("dialog", { name: "Placement of Review" });
    await dialog.getByRole("radio", { name: "On a machine" }).check();
    await dialog.getByRole("combobox", { name: "Machine" }).selectOption("spark");
    await dialog.getByRole("button", { name: "Apply" }).click();
    await expect(page.getByRole("button", { name: "on spark" })).toBeVisible();
    // Fetch diff (spark) -> Review (now spark) is no longer a hop.
    await expect.poll(() => dash(edge(page, "fetch-diff.diff->review.diff"))).toBe("none");
    await expect(step(page, "Review")).toHaveAttribute("data-machine-slot", "0");

    const toggle = step(page, "Run tests").getByRole("switch", { name: "Run tests enabled" });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-checked", "false");

    await page.getByRole("button", { name: "Save" }).click();
    await expect.poll(() => calls.filter((c) => c.method === "PUT").length).toBe(1);
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.path).toBe("/api/workflows/review-pr");
    const body = put.body as { steps: { id: string; placement: unknown; enabled: boolean }[] };
    expect(body.steps.find((s) => s.id === "review")!.placement).toEqual({ machine: "spark" });
    expect(body.steps.find((s) => s.id === "run-tests")!.enabled).toBe(false);
    await expect(page.getByRole("button", { name: "Save" })).toHaveCount(0);
  });

  test("typed ports: a drag between mismatched types is refused, a matching one wires", async ({ page }) => {
    await open(page);
    const handle = (name: string, port: string) =>
      step(page, name).locator(`[data-port="${port}"] .react-flow__handle`);
    await expect(handle("Review", "in:diff")).toHaveAttribute("data-port-type", "string");

    // boolean -> string: refused.
    await handle("Run tests", "out:passed").dragTo(handle("Review", "in:diff"));
    await expect(edge(page, "run-tests.passed->review.diff")).toHaveCount(0);
    await expect(edge(page, "fetch-diff.diff->review.diff")).toHaveCount(1);

    // string -> string: wires, replacing what fed Review's diff.
    await handle("Inputs", "out:repo").dragTo(handle("Review", "in:diff"));
    await expect(edge(page, "inputs.repo->review.diff")).toHaveCount(1);
    await expect(edge(page, "fetch-diff.diff->review.diff")).toHaveCount(0);
  });

  test("deletes a step and adds one", async ({ page }) => {
    await open(page);
    await step(page, "Run tests").locator(".wf-card__name").click();
    await page.getByRole("button", { name: "Delete Run tests" }).click();
    await expect(step(page, "Run tests")).toHaveCount(0);
    await expect(edge(page, "run-tests.passed->decide.passed")).toHaveCount(0);
    await page.getByRole("button", { name: "Add step" }).click();
    await expect(step(page, "New step")).toBeVisible();
    expect((await agentState(page)).workflows.dirty).toBe(true);
  });

  test("Import, Export and the repo picker call the io endpoints", async ({ page }) => {
    const calls = await open(page);
    expect(calls.some((c) => c.method === "GET" && c.path === "/api/repos")).toBe(true);
    await page.getByRole("button", { name: /agentculture\/workflows/ }).click();
    await page.getByRole("option", { name: "agentculture/rules-lab" }).click();
    await expect(page.getByRole("button", { name: /agentculture\/rules-lab/ })).toBeVisible();

    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export", exact: true }).click();
    expect((await download).suggestedFilename()).toMatch(/\.json$/);
    expect(calls.some((c) => c.method === "GET" && c.path === "/api/export" && c.search === "?format=json")).toBe(true);

    await page.getByLabel("Import files").setInputFiles({
      name: IMPORT_FILE_NAME,
      mimeType: "application/json",
      buffer: Buffer.from(IMPORT_FILE_TEXT),
    });
    const plan = page.getByRole("dialog", { name: "Import plan" });
    await expect(plan).toContainText("workflows/triage.json");
    await plan.getByRole("button", { name: "Apply import" }).click();
    await expect(plan).toHaveCount(0);
    const imports = calls.filter((c) => c.method === "POST" && c.path === "/api/import");
    expect(imports.map((c) => (c.body as { apply: boolean }).apply)).toEqual([false, true]);

    // The picked repository: export into it (a commit) and import from it, each dry-run first.
    await page.getByRole("button", { name: /agentculture\/rules-lab/ }).click();
    await page.getByRole("button", { name: "Export to repo" }).click();
    const exportPlan = page.getByRole("dialog", { name: "Export plan" });
    await expect(exportPlan).toContainText("workflows/review-pr.json");
    await exportPlan.getByRole("button", { name: "Apply export" }).click();
    await expect(page.getByText(/Exported 1 change to agentculture\/rules-lab \(0123456\)/)).toBeVisible();
    const toRepo = calls.filter((c) => c.method === "POST" && c.path === "/api/export").map((c) => c.body);
    expect(toRepo).toEqual([
      { repo: "agentculture/rules-lab", apply: false },
      { repo: "agentculture/rules-lab", apply: true },
    ]);

    await page.getByRole("button", { name: /agentculture\/rules-lab/ }).click();
    await page.getByRole("button", { name: "Import from repo" }).click();
    await page.getByRole("dialog", { name: "Import plan" }).getByRole("button", { name: "Apply import" }).click();
    const importCalls = () => calls.filter((c) => c.method === "POST" && c.path === "/api/import");
    await expect.poll(() => importCalls().length).toBe(4);
    const fromRepo = importCalls().slice(2);
    expect(fromRepo.map((c) => c.body)).toEqual([
      { repo: "agentculture/rules-lab", apply: false },
      { repo: "agentculture/rules-lab", apply: true },
    ]);
  });

  test("a run lights its path with each step's host and outcome (persisted run state)", async ({ page }) => {
    const calls = await open(page, "/workflows?id=review-pr&run=run-7");
    expect(calls.some((c) => c.path === "/api/runs/run-7")).toBe(true);
    await expect(step(page, "Decide")).toHaveAttribute("data-run-status", "failed");
    await expect(step(page, "Decide")).toContainText("failed on spark");
    await expect(step(page, "Review")).toContainText("succeeded on thor");
    await expect(step(page, "Run tests")).toContainText("succeeded on spark2");
    await expect(step(page, "Fetch diff")).toContainText("succeeded on spark");
    await expect(edge(page, "review.findings->decide.findings")).toHaveClass(/is-lit/);
    await expect(edge(page, "decide.verdict->outputs.verdict")).not.toHaveClass(/is-lit/);
    const state = await agentState(page);
    expect(state.workflows.run).toEqual({ id: "run-7", status: "failed" });
    // `animations: "disabled"` fast-forwards the lit edges' transition to its end state.
    await page.screenshot({ path: SCREENSHOT.replace(/\.png$/, "-run.png"), fullPage: true, animations: "disabled" });
  });

  test("keyboard: a step is reachable, Enter selects it, its toolbar is operable", async ({ page }) => {
    await open(page);
    await step(page, "Review").focus();
    await page.keyboard.press("Enter");
    const chip = page.getByRole("button", { name: "on thor" });
    await expect(chip).toBeVisible();
    await chip.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog", { name: "Placement of Review" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Placement of Review" })).toHaveCount(0);
  });

  test("axe: no serious or critical violations, also with a run and an open editor", async ({ page }) => {
    await open(page, "/workflows?id=review-pr&run=run-7");
    const check = async (label: string) => {
      const results = await new AxeBuilder({ page }).analyze();
      const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(bad.map((v) => `${label}: ${v.id} — ${v.help} (${v.nodes.map((n) => n.target).join(" ")})`)).toEqual([]);
    };
    await check("board");
    await step(page, "Decide").locator(".wf-card__name").click();
    await page.getByRole("button", { name: "Edit Decide" }).click();
    await expect(page.getByRole("dialog", { name: "Edit Decide" })).toBeVisible();
    await check("editor");
  });

  test("zoom (d19): buttons, keys and ctrl + wheel zoom; a plain wheel scrolls the page", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 600 }); // a page taller than the window
    await open(page);
    const zoom = page.getByRole("group", { name: "Zoom" });
    const level = page.getByTestId("zoom-level");
    const card = async () => Math.round((await step(page, "Review").boundingBox())!.width);
    await expect(level).toHaveText(/100%/);
    expect(await card()).toBe(190);
    // Large targets: every zoom button is at least 44px square.
    for (const name of ["Zoom out", "Zoom in", "Fit to width"]) {
      const box = (await zoom.getByRole("button", { name }).boundingBox())!;
      expect(Math.min(box.width, box.height)).toBeGreaterThanOrEqual(44);
    }
    await zoom.getByRole("button", { name: "Zoom out" }).click();
    await expect(level).toHaveText(/80%/);
    await expect.poll(card).toBe(152);
    // Keyboard, with focus in the canvas.
    await step(page, "Review").focus();
    await page.keyboard.press("+");
    await page.keyboard.press("+");
    await expect(level).toHaveText(/125%/);
    // 0 fits the graph to the canvas width: no more sideways scrolling.
    const scroller = page.locator(".wf-canvas__scroll");
    const overflow = () => scroller.evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(await overflow()).toBeGreaterThan(0); // the board's graph is wider than the canvas
    await page.keyboard.press("0");
    await expect(level).not.toHaveText(/125%/);
    await expect.poll(overflow).toBeLessThanOrEqual(1);
    const fitted = await level.textContent();
    // A plain wheel over the canvas scrolls the page and leaves the zoom alone.
    const canvas = page.getByRole("region", { name: "Workflow canvas" });
    const box = (await canvas.boundingBox())!;
    await page.mouse.move(box.x + 30, box.y + box.height / 2);
    await page.mouse.wheel(0, 200);
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
    await expect(level).toHaveText(fitted!);
    // ctrl + wheel (a trackpad pinch arrives as one) zooms.
    await page.keyboard.down("Control");
    await page.mouse.wheel(0, 300);
    await page.keyboard.up("Control");
    await expect.poll(async () => parseInt((await level.textContent())!.replace(/\D/g, ""), 10)).toBeLessThan(parseInt(fitted!.replace(/\D/g, ""), 10));
  });

  test("(i) on the head and the list row: mouse and keyboard, the API's lines, focus return (d19)", async ({ page }) => {
    await open(page);
    const head = page.locator(".wf-head");
    const about = head.getByRole("button", { name: "About Review PR" });
    const box = (await about.boundingBox())!;
    expect(Math.min(box.width, box.height)).toBeGreaterThanOrEqual(36); // 44px hit area via d4
    await expect(about).toHaveAttribute("aria-expanded", "false");
    await about.click();
    const panel = head.getByRole("dialog", { name: "About Review PR" });
    await expect(panel.getByTestId("about-lines")).toHaveText(REVIEW_PR_DESCRIBED.lines.join("\n"));
    await expect(panel).toBeFocused();
    expect(await panel.getByTestId("about-lines").evaluate((el) => getComputedStyle(el).whiteSpace)).toBe("pre");
    const results = await new AxeBuilder({ page }).analyze();
    const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${v.id} — ${v.help}`)).toEqual([]);
    await page.keyboard.press("Escape");
    await expect(panel).toBeHidden();
    await expect(about).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(panel).toBeVisible();
    await page.keyboard.press("Escape");
    await page.keyboard.press(" ");
    await expect(panel).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(about).toBeFocused();
    // The list row's (i) opens without opening the row (no navigation).
    const url = page.url();
    const list = page.getByRole("navigation", { name: "Workflows" });
    await list.getByRole("button", { name: "About Review PR" }).click();
    await expect(list.getByRole("dialog", { name: "About Review PR" }).getByTestId("about-lines")).toBeVisible();
    expect(page.url()).toBe(url);
    // A press elsewhere closes it.
    await page.getByRole("heading", { level: 1, name: "Review PR" }).click();
    await expect(list.getByRole("dialog", { name: "About Review PR" })).toBeHidden();
  });

  test("(i) docks to the bottom of the screen at phone width (d19)", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 800 });
    await open(page);
    await page.locator(".wf-head").getByRole("button", { name: "About Review PR" }).click();
    const panel = page.locator(".wf-head").getByRole("dialog", { name: "About Review PR" });
    await expect(panel.getByTestId("about-lines")).toBeVisible();
    const b = (await panel.boundingBox())!;
    expect(b.x).toBeGreaterThanOrEqual(0);
    expect(b.x + b.width).toBeLessThanOrEqual(390);
  });

  test("prefers-reduced-motion disables the canvas transitions", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await open(page);
    const card = step(page, "Review").locator(".wf-card");
    const d = await card.evaluate((el) => getComputedStyle(el).transitionDuration);
    expect(d.split(",").every((v) => parseFloat(v) <= 0.00001)).toBe(true);
    // A button zoom lands at once (no animated transition) under reduced motion (d19).
    await page.getByRole("button", { name: "Zoom in" }).click();
    const transform = await page
      .locator(".wf-canvas .react-flow__viewport")
      .evaluate((el) => (el as HTMLElement).style.transform);
    expect(transform).toContain("scale(1.25)");
  });
});
