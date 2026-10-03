import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./fixtures/api";
import { mockActorsApi } from "./fixtures/actors";

/** Where the Actors screenshot lands, for review against the 'Chosen — Actors' board. */
const SCREENSHOT = process.env.ACTORS_SCREENSHOT ?? "test-results/actors.png";

async function agentState(page: Page) {
  return JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}");
}

async function open(page: Page, path = "/actors") {
  await mockApi(page);
  // Registered last, so it answers /actors* before the shared fixture does.
  const calls = await mockActorsApi(page);
  await page.goto(path);
  await expect.poll(async () => (await agentState(page)).status).toBe("ready");
  return calls;
}

const style = (locator: ReturnType<Page["locator"]>, prop: string) =>
  locator.evaluate((el, p) => getComputedStyle(el).getPropertyValue(p), prop);

test.describe("Actors tab", () => {
  test("#agent-state is ready with the roster, no console errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    await open(page);
    const state = await agentState(page);
    expect(state.tab).toBe("actors");
    expect(state.errors).toEqual([]);
    expect(state.actors).toEqual({ count: 8, shown: 8, kind: "all", selected: "claude-code" });
    expect(errors).toEqual([]);
  });

  test("matches the 'Chosen — Actors' board: layout, type scale, machine colors (screenshot)", async ({ page }) => {
    await open(page);
    const card = page.getByRole("group", { name: "Claude Code" });
    await expect(card.getByRole("radiogroup", { name: "Configuration source" })).toBeVisible();

    // A 1000px column, centred (140px each side at the board's 1280px).
    const roster = await page.locator(".actors-roster").boundingBox();
    expect(roster!.width).toBe(1000);
    expect(roster!.x).toBe(140);

    // Type scale: Fraunces 32px selected name, 30px other rows, 17px kind, 15px filters/caps.
    const selectedName = card.getByRole("button", { name: "Claude Code", exact: true });
    expect(await style(selectedName, "font-size")).toBe("32px");
    expect(await style(selectedName, "font-family")).toContain("Fraunces");
    const codexName = page.getByRole("group", { name: "Codex" }).getByRole("button", { name: "Codex", exact: true });
    expect(await style(codexName, "font-size")).toBe("30px");
    expect(await style(page.getByRole("group", { name: "Codex" }).getByText("agent"), "font-size")).toBe("17px");
    expect(await style(page.getByRole("button", { name: "Agents" }), "font-size")).toBe("15px");
    expect(await style(card.getByText("triage"), "font-size")).toBe("15px");

    // Controls: filter pills 40px, Add actor 44px teal, switch 46x28, edit/delete 44px.
    expect((await page.getByRole("button", { name: "Agents" }).boundingBox())!.height).toBe(40);
    expect((await page.getByRole("button", { name: "Add actor" }).boundingBox())!.height).toBe(44);
    const sw = await card.getByRole("switch").boundingBox();
    expect([sw!.width, sw!.height]).toEqual([46, 28]);
    expect((await card.getByRole("button", { name: "Edit Claude Code" }).boundingBox())!.width).toBe(44);

    // Machine colors: spark teal, thor amber, spark2 blue.
    const dot = (name: string) =>
      style(page.getByRole("group", { name }).locator(".machine-dot"), "background-color");
    expect(await dot("Claude Code")).toBe("rgb(10, 138, 120)");
    expect(await dot("Codex")).toBe("rgb(180, 83, 31)");
    expect(await dot("Colleague")).toBe("rgb(59, 79, 176)");
    // The selected card is ringed in its machine's color.
    expect(await style(card, "box-shadow")).toContain("rgb(10, 138, 120)");

    await page.screenshot({ path: SCREENSHOT, fullPage: true });
  });

  test("kind filter, selection and toggle work together", async ({ page }) => {
    const calls = await open(page);
    await page.getByRole("button", { name: "Humans" }).click();
    await expect(page.getByRole("group")).toHaveCount(1);
    expect((await agentState(page)).actors).toMatchObject({ shown: 1, kind: "human", selected: "ori" });
    await page.getByRole("button", { name: "All" }).click();
    await page.getByRole("group", { name: "Codex" }).getByRole("button", { name: "Codex", exact: true }).click();
    await expect(page).toHaveURL(/\?id=codex$/);
    await expect(page.getByRole("group", { name: "Codex" }).getByText("db record")).toBeVisible();
    await page.getByRole("group", { name: "Codex" }).getByRole("switch").click();
    await expect(page.getByRole("group", { name: "Codex" }).getByRole("switch")).toHaveAttribute("aria-checked", "false");
    expect(calls.map((c) => `${c.method} ${c.path}`)).toContain("POST /api/actors/codex/disable");
  });

  test("edit saves a PUT; delete asks first, then DELETEs", async ({ page }) => {
    const calls = await open(page, "/actors?id=claude-code");
    const card = page.getByRole("group", { name: "Claude Code" });
    await card.getByRole("button", { name: "Edit Claude Code" }).click();
    await card.getByRole("textbox", { name: "Model" }).fill("opus 5");
    await card.getByRole("button", { name: "Save" }).click();
    await expect(card.getByText("opus 5")).toBeVisible();
    expect(calls.find((c) => c.method === "PUT")?.path).toBe("/api/actors/claude-code");

    await card.getByRole("button", { name: "Delete Claude Code" }).click();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await card.getByRole("button", { name: "Confirm delete" }).click();
    await expect(page.getByRole("group", { name: "Claude Code" })).toHaveCount(0);
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/actors/claude-code")).toBe(true);
  });

  test("keyboard: Tab reaches filters, rows and switches; Enter selects a row", async ({ page }) => {
    await open(page);
    await page.getByRole("button", { name: "All" }).focus();
    const names: string[] = [];
    for (let i = 0; i < 20; i++) {
      await page.keyboard.press("Tab");
      names.push(
        await page.evaluate(() => {
          const el = document.activeElement as HTMLElement;
          return (el.getAttribute("aria-label") ?? el.textContent ?? "").trim();
        }),
      );
    }
    expect(names).toContain("Codex");
    expect(names).toContain("Codex enabled");
    expect(await page.evaluate(() => getComputedStyle(document.activeElement as Element).outlineStyle)).not.toBe("none");
    await page.getByRole("group", { name: "Colleague" }).getByRole("button", { name: "Colleague" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("group", { name: "Colleague" }).getByRole("radiogroup")).toBeVisible();
  });

  test("axe: no serious or critical violations, collapsed, expanded and editing", async ({ page }) => {
    await open(page);
    const bad = async () =>
      (await new AxeBuilder({ page }).analyze()).violations
        .filter((v) => v.impact === "serious" || v.impact === "critical")
        .map((v) => `${v.id} — ${v.help}`);
    expect(await bad()).toEqual([]);
    await page.getByRole("group", { name: "Reachy" }).getByRole("button", { name: "Reachy" }).click();
    expect(await bad()).toEqual([]);
    await page.getByRole("button", { name: "Edit Reachy" }).click();
    expect(await bad()).toEqual([]);
  });

  test("prefers-reduced-motion disables the row and switch transitions", async ({ page }) => {
    await open(page);
    const sw = page.getByRole("group", { name: "Codex" }).getByRole("switch");
    await page.emulateMedia({ reducedMotion: "no-preference" });
    expect(await style(sw, "transition-duration")).not.toBe("0s");
    await page.emulateMedia({ reducedMotion: "reduce" });
    const still = await style(sw, "transition-duration");
    expect(still.split(",").every((d) => parseFloat(d) <= 0.00001)).toBe(true);
  });
});
