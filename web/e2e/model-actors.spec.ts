import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./fixtures/api";
import { mockActorsApi } from "./fixtures/actors";
import { mockModelActorsApi, type ModelActorsOptions } from "./fixtures/model-actors";

/**
 * Model actors on the Actors tab (t10): Add models from a gateway, Used by, Move, and the
 * in-use Disable refusal, against the mocked API. When MODEL_ACTORS_SHOTS names a
 * directory, each state is also captured there in light and dark (for the operator's
 * review; never committed).
 */
const SHOTS = process.env.MODEL_ACTORS_SHOTS;

async function agentState(page: Page) {
  return JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}");
}

async function open(page: Page, options: ModelActorsOptions & { roles?: string[] } = {}, path = "/actors") {
  await mockApi(page, options.roles ? { roles: options.roles } : {});
  const actorCalls = await mockActorsApi(page);
  const calls = await mockModelActorsApi(page, options);
  await page.goto(path);
  await expect.poll(async () => (await agentState(page)).status).toBe("ready");
  return { calls, actorCalls };
}

async function seriousViolations(page: Page) {
  return (await new AxeBuilder({ page }).analyze()).violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id} — ${v.help}`);
}

/** Capture the state in light and dark when MODEL_ACTORS_SHOTS is set. */
async function shoot(page: Page, name: string) {
  if (!SHOTS) return;
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
    await page.screenshot({ path: `${SHOTS}/${name}-${scheme}.png`, fullPage: true });
  }
  await page.emulateMedia({ colorScheme: "light" });
}

const usedBy = (page: Page) => page.getByRole("region", { name: "Claude Code used by" });

test.describe("Actors tab: model actors", () => {
  test("Add models: gateways from model_gateways, match and pools, the dry-run plan, then apply", async ({ page }) => {
    const { calls } = await open(page);
    await page.getByRole("button", { name: "Add models" }).click();
    const panel = page.getByRole("region", { name: "Add models" });
    await panel.getByRole("checkbox", { name: /^spark http/ }).check();
    await panel.getByRole("textbox", { name: "Models to match" }).fill("qwen*");
    await panel.getByRole("textbox", { name: "Pools" }).fill("qwen3-coder=coder");
    await panel.getByRole("button", { name: "Plan" }).click();
    const plan = panel.getByRole("list", { name: "Plan" });
    await expect(plan.getByRole("listitem")).toHaveCount(4);
    await expect(plan.getByRole("listitem").first()).toContainText("qwen3-coder");
    expect(calls.find((c) => c.path === "/api/actors/enrol-models")?.body).toEqual({
      gateways: ["spark"],
      match: ["qwen*"],
      pools: ["qwen3-coder=coder"],
      dry_run: true,
    });
    expect(await seriousViolations(page)).toEqual([]);
    await shoot(page, "a-add-models-plan");

    await panel.getByRole("button", { name: "Apply 2 changes" }).click();
    await expect(panel.getByRole("status")).toContainText("Added qwen3-coder");
    await expect(panel.getByRole("alert")).toContainText("qwen3-vl is held by vision");
    expect(calls.filter((c) => c.path === "/api/actors/enrol-models").at(-1)?.body).toMatchObject({ dry_run: false });
  });

  test("Used by lists the references, each linking its workflow; Move repoints with the revision read", async ({ page }) => {
    const { calls } = await open(page);
    const section = usedBy(page);
    await expect(section.getByRole("link", { name: "newspaper" })).toHaveAttribute("href", "/workflows?id=newspaper");
    await expect(section.getByRole("link", { name: "nightly-news" })).toHaveAttribute("href", "/rules/nightly-news");
    await expect(section.getByRole("button", { name: /^Move / })).toHaveCount(2);
    expect(await seriousViolations(page)).toEqual([]);
    await shoot(page, "b-used-by");

    await section.getByRole("button", { name: "Move newspaper" }).click();
    const form = section.getByRole("form", { name: "Move newspaper" });
    await form.getByRole("combobox", { name: "To" }).selectOption("codex");
    await form.getByRole("button", { name: "Move" }).click();
    await expect(section.getByRole("status")).toContainText("Moved newspaper to Codex");
    await expect(section.getByRole("link", { name: "newspaper" })).toHaveCount(0);
    expect(calls.find((c) => c.path === "/api/workflows/newspaper/repoint-actor")?.body).toEqual({
      from: "claude-code",
      to: "codex",
      expected_revision: 4,
      force: false,
      dry_run: false,
    });

    // Following a link opens the workflow.
    await section.getByRole("link", { name: "review-pr" }).click();
    await expect(page).toHaveURL(/\/workflows\?id=review-pr$/);
  });

  test("Move on a stale revision: the workflow changed since you opened it, Reload, then Move", async ({ page }) => {
    const { calls } = await open(page, { staleOnce: true });
    const section = usedBy(page);
    await section.getByRole("button", { name: "Move newspaper" }).click();
    await section.getByRole("combobox", { name: "To" }).selectOption("codex");
    await section.getByRole("button", { name: "Move", exact: true }).click();
    const alert = section.getByRole("alert");
    await expect(alert).toContainText("This workflow changed since you opened it.");
    await expect(alert.getByRole("button", { name: "Reload" })).toBeVisible();
    expect(await seriousViolations(page)).toEqual([]);
    await shoot(page, "c-move-stale-reload");

    await alert.getByRole("button", { name: "Reload" }).click();
    await expect(section.getByRole("alert")).toHaveCount(0);
    await section.getByRole("button", { name: "Move", exact: true }).click();
    await expect(section.getByRole("status")).toContainText("Moved newspaper to Codex");
    const repoints = calls.filter((c) => c.path === "/api/workflows/newspaper/repoint-actor");
    expect(repoints.map((c) => (c.body as { expected_revision: number }).expected_revision)).toEqual([4, 5]);
  });

  test("Disable while in use: the refusal names the workflows; Disable anyway forces it", async ({ page }) => {
    const { calls } = await open(page);
    const row = page.getByRole("group", { name: "Claude Code" });
    await row.getByRole("switch", { name: "Claude Code enabled" }).click();
    const refusal = row.getByRole("alert");
    await expect(refusal).toContainText("Claude Code is still used by");
    await expect(refusal.getByRole("link", { name: "newspaper" })).toHaveAttribute("href", "/workflows?id=newspaper");
    await expect(refusal.getByRole("link", { name: "review-pr" })).toBeVisible();
    await expect(row.getByRole("switch")).toHaveAttribute("aria-checked", "true");
    expect(await seriousViolations(page)).toEqual([]);
    await shoot(page, "d-disable-in-use");

    await refusal.getByRole("button", { name: "Disable anyway" }).click();
    await expect(row.getByRole("switch")).toHaveAttribute("aria-checked", "false");
    await expect(row.getByRole("alert")).toHaveCount(0);
    expect(calls.some((c) => c.path === "/api/actors/claude-code/disable" && c.search === "?force=true")).toBe(true);
  });

  test("keyboard: Move opens, picks a target and submits without a pointer", async ({ page }) => {
    await open(page);
    const section = usedBy(page);
    await section.getByRole("button", { name: "Move newspaper" }).focus();
    await page.keyboard.press("Enter");
    const to = section.getByRole("combobox", { name: "To" });
    await expect(to).toBeVisible();
    await to.focus();
    await to.selectOption("codex");
    // Move waits for the workflow's revision to be read.
    const move = section.getByRole("button", { name: "Move", exact: true });
    await expect(move).toBeEnabled();
    await page.keyboard.press("Tab");
    await expect(move).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(section.getByRole("status")).toContainText("Moved newspaper to Codex");
  });

  test("non-admins get no Add models; viewers get no Move", async ({ page }) => {
    await open(page, { roles: ["viewer"] });
    await expect(usedBy(page).getByRole("link", { name: "newspaper" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Add models" })).toHaveCount(0);
    await expect(usedBy(page).getByRole("button", { name: /^Move/ })).toHaveCount(0);
  });

  test("an older server (the routes 404): no Add models, Used by or Move; Disable is plain", async ({ page }) => {
    const { calls, actorCalls } = await open(page, { old: true });
    await expect.poll(() => calls.some((c) => c.path.endsWith("/references"))).toBe(true);
    await expect(page.getByRole("button", { name: "Add models" })).toHaveCount(0);
    await expect(page.getByRole("region", { name: /used by/ })).toHaveCount(0);
    const row = page.getByRole("group", { name: "Claude Code" });
    await row.getByRole("switch").click();
    await expect(row.getByRole("switch")).toHaveAttribute("aria-checked", "false");
    expect(actorCalls.some((c) => c.method === "POST" && c.path === "/api/actors/claude-code/disable")).toBe(true);
  });
});
