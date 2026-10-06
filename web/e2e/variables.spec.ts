import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockRulesApi } from "./fixtures/rules";

async function untilReady(page: Page) {
  await expect
    .poll(async () => JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}").status)
    .toBe("ready");
}

test.describe("Variables tab", () => {
  test("lists variables with their history and the rules that use them", async ({ page }) => {
    await mockRulesApi(page);
    await page.goto("/variables");
    await untilReady(page);
    await expect(page.getByRole("heading", { level: 1, name: "Variables" })).toBeVisible();
    const list = page.getByRole("navigation", { name: "Variables" });
    await expect(list.getByRole("link")).toHaveCount(2);
    await expect(page.getByRole("list", { name: "Version history" }).getByRole("listitem")).toHaveCount(2);
    await expect(page.getByRole("list", { name: "Used by rules" }).getByRole("link")).toHaveText(
      "Build and publish",
    );
    const state = JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}");
    expect(state.tab).toBe("variables");
    expect(state.errors).toEqual([]);
    const bad = (await new AxeBuilder({ page }).analyze()).violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(bad.map((v) => v.id)).toEqual([]);
  });

  test("an admin edits a list from the keyboard and a new version is appended", async ({ page }) => {
    const api = await mockRulesApi(page);
    await page.goto("/variables");
    await untilReady(page);
    const items = page.getByLabel("Items, one per line");
    await items.focus();
    await page.keyboard.press("Control+End");
    await page.keyboard.type("\nmonalisa");
    await page.getByRole("button", { name: "Save new version" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("list", { name: "Version history" }).getByRole("listitem").first()).toContainText("v3");
    const put = api.calls.find((c) => c.method === "PUT" && c.path === "/variables/trusted_authors");
    expect(put?.body).toMatchObject({ value: ["octocat", "hubot", "monalisa"] });
  });

  test("reduced motion: the list rows do not animate", async ({ page }) => {
    await mockRulesApi(page);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/variables");
    await untilReady(page);
    const row = page.getByRole("navigation", { name: "Variables" }).getByRole("link").first();
    const durations = await row.evaluate((el) => getComputedStyle(el).transitionDuration);
    expect(durations.split(",").every((d) => parseFloat(d) <= 0.00001)).toBe(true);
  });
});

test.describe("Condition editor: variable picker", () => {
  test("an author check picks vars.trusted_authors and saves a var operand", async ({ page }) => {
    const api = await mockRulesApi(page);
    await page.goto("/rules/train-batch");
    await untilReady(page);
    await page.getByRole("button", { name: "Add stage" }).click();
    await page.getByRole("button", { name: "Add condition" }).click();
    const form = page.getByRole("form", { name: "Add condition" });
    await form.getByLabel("Variable").fill("trigger.data.author");
    await form.getByLabel("Comparison").selectOption("in");
    await form.getByLabel("Allowed list").selectOption({ label: "vars.trusted_authors" });
    await form.getByRole("button", { name: "Add" }).click();
    await expect(page.getByTestId("stage-condition")).toContainText("is in vars.trusted_authors");
    const put = api.calls.find((c) => c.method === "PUT" && c.path === "/rules/train-batch");
    expect((put?.body as { condition: unknown }).condition).toEqual({
      op: "in",
      value: { field: "data.author" },
      items: { var: "trusted_authors" },
    });
  });
});
