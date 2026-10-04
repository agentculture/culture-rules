import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import type { FakeApi } from "../src/rules/fake-api";
import { mockRulesApi } from "./fixtures/rules";

/**
 * The typed trigger picker and the typed action picker, driven through the
 * New rule form. The fake API records every request body, so each test
 * asserts exactly what would be saved.
 */
async function untilReady(page: Page) {
  await expect
    .poll(async () => JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}").status)
    .toBe("ready");
}

async function noSeriousAxe(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${label}: ${v.id} — ${v.help} (${v.nodes.map((n) => n.target).join(" ")})`)).toEqual([]);
}

const created = (api: FakeApi) =>
  api.calls.filter((c) => c.method === "POST" && c.path === "/rules").map((c) => c.body as Record<string, any>);

async function openNewRule(page: Page, name: string) {
  const api = await mockRulesApi(page);
  await page.goto("/rules");
  await untilReady(page);
  await page.getByRole("button", { name: "New rule" }).click();
  const form = page.getByRole("form", { name: "New rule" });
  await form.getByLabel("Name").fill(name);
  return { api, form };
}

test.describe("Trigger picker", () => {
  test("app surface then a declared event writes trigger.params.type; no free-text event field", async ({ page }) => {
    const { api, form } = await openNewRule(page, "On PR opened");
    const surface = form.getByLabel("Surface");
    // A disabled app is not offered.
    await expect(surface.locator("option", { hasText: "Discord" })).toHaveCount(0);
    await surface.selectOption("github-app");
    const events = await form.getByLabel("Event", { exact: true }).locator("option").evaluateAll((os) =>
      os.map((o) => (o as HTMLOptionElement).value),
    );
    expect(events).toEqual(["", "github.pr.opened", "github.push"]);
    await form.getByLabel("Event", { exact: true }).selectOption("github.pr.opened");
    await noSeriousAxe(page, "new rule, event trigger");
    await form.getByRole("button", { name: "Create rule" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "On PR opened" })).toBeVisible();
    expect(created(api)).toHaveLength(1);
    expect(created(api)[0].trigger).toEqual({ kind: "event", params: { type: "github.pr.opened" } });
  });

  test("an event trigger without an event is refused with a guided notice and nothing is sent", async ({ page }) => {
    const { api, form } = await openNewRule(page, "Incomplete");
    await form.getByRole("button", { name: "Create rule" }).click();
    await expect(form.getByRole("alert").or(form.getByRole("status"))).toBeVisible();
    expect(created(api)).toHaveLength(0);
  });

  test("a schedule preset writes its cron", async ({ page }) => {
    const { api, form } = await openNewRule(page, "Hourly sweep");
    await form.getByRole("radio", { name: "On a schedule" }).check();
    await form.getByLabel("Repeat").selectOption("Every hour");
    await expect(page.getByTestId("cron-words")).toBeVisible();
    await noSeriousAxe(page, "new rule, schedule trigger");
    await form.getByRole("button", { name: "Create rule" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Hourly sweep" })).toBeVisible();
    expect(created(api)[0].trigger).toMatchObject({ kind: "schedule", params: { cron: "0 * * * *" } });
  });

  test("a probe writes actor, command, mode and schedule", async ({ page }) => {
    const { api, form } = await openNewRule(page, "Disk probe");
    await form.getByRole("radio", { name: "A probe" }).check();
    await form.getByLabel("Actor").first().selectOption("ci-runner");
    await form.getByLabel("Command").selectOption("disk-free");
    await form.getByLabel("Mode").selectOption("condition");
    await form.getByLabel("Repeat").selectOption("Every 5 minutes");
    await noSeriousAxe(page, "new rule, probe trigger");
    await form.getByRole("button", { name: "Create rule" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Disk probe" })).toBeVisible();
    expect(created(api)[0].trigger).toEqual({
      kind: "probe",
      params: { actor: "ci-runner", command: "disk-free", mode: "condition", schedule: "*/5 * * * *" },
    });
  });
});

test.describe("Action picker", () => {
  test("github.comment with an actor and number mapped to trigger.data.number shows a chip and saves the ref", async ({ page }) => {
    const { api, form } = await openNewRule(page, "Thank the author");
    await form.getByLabel("Surface").selectOption("github-app");
    await form.getByLabel("Event", { exact: true }).selectOption("github.pr.opened");

    await form.getByLabel("What happens").selectOption("github.comment");
    // Only actors that support the kind are offered.
    const actors = await form.getByLabel("Actor").last().locator("option").evaluateAll((os) =>
      os.map((o) => (o as HTMLOptionElement).value),
    );
    expect(actors).toEqual(["", "github-app"]);
    await form.getByLabel("Actor").last().selectOption("github-app");
    await form.getByRole("textbox", { name: "Repo" }).fill("acme/app");
    await form.getByLabel("Map Number").selectOption("trigger.data.number");
    await form.getByRole("textbox", { name: "Body" }).fill("Thanks!");

    const chip = page.getByTestId("chip-number");
    await expect(chip).toContainText("trigger.data.number");
    await expect(form.getByRole("textbox", { name: "Number" })).toHaveCount(0);
    await noSeriousAxe(page, "new rule, github.comment action");

    await form.getByRole("button", { name: "Create rule" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Thank the author" })).toBeVisible();
    expect(created(api)[0].action).toMatchObject({
      kind: "github.comment",
      params: { actor: "github-app", repo: "acme/app", number: "trigger.data.number", body: "Thanks!" },
    });
  });

  test("an incomplete action is refused with a guided notice", async ({ page }) => {
    const { api, form } = await openNewRule(page, "No actor");
    await form.getByLabel("Surface").selectOption("github-app");
    await form.getByLabel("Event", { exact: true }).selectOption("github.push");
    await form.getByLabel("What happens").selectOption("github.comment");
    await form.getByRole("button", { name: "Create rule" }).click();
    await expect(form.getByRole("alert").or(form.getByRole("status"))).toBeVisible();
    expect(created(api)).toHaveLength(0);
  });
});
