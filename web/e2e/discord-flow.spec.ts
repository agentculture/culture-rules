import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { ACTORS } from "../src/fixtures/rules-fixture";
import { mockRulesApi } from "./fixtures/rules";
import { withView } from "./fixtures/view";

/**
 * discord-surfaces t9: a Discord channel message becomes a Markdown page and goes to a library.
 * The rule's trigger filters (channels picked from the bot, All, users, roles, bots) are saved in
 * trigger params; the workflow it starts is a markdown.render built-in step then a library.push
 * step. The fake API records each request body, so every assertion is on what would be saved.
 */
const BOT = {
  id: "discord-bot",
  name: "Community bot",
  kind: "app",
  enabled: true,
  params: { surface: "discord", events: ["discord.message.created"], actions: ["discord.read"], connection: {} },
};
const TARGETS = {
  guilds: [
    {
      id: "10",
      name: "Lab",
      channels: [
        { id: "101", name: "general", visible: true },
        { id: "102", name: "reading", visible: true },
      ],
    },
  ],
};

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

test("a channel message goes through markdown.render and library.push", async ({ page }) => {
  await withView(page);
  const api = await mockRulesApi(page);
  // Routes registered later win: an enabled Discord bot and what it can see.
  await page.route("**/api/actors", (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify({ items: [...ACTORS, BOT] }) }),
  );
  await page.route("**/api/actors/discord-bot/discord/targets", (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify(TARGETS) }),
  );
  await page.goto("/workflows");
  await untilReady(page);

  // 1. The trigger: a Discord event, one channel, a user, bots included.
  await page.getByRole("button", { name: "New rule" }).first().click();
  const form = page.getByRole("form", { name: "New rule" });
  await form.getByLabel("Name").fill("Discord to library");
  await form.getByLabel("Surface").selectOption("discord-bot");
  await form.getByLabel("Event", { exact: true }).selectOption("discord.message.created");
  const channels = form.getByRole("group", { name: "Channels" });
  await expect(channels.getByRole("radio", { name: "All channels" })).toBeChecked();
  await channels.getByRole("radio", { name: "Only these channels" }).check();
  await channels.getByRole("checkbox", { name: "#general" }).check();
  await form.getByLabel("Users").fill("ori");
  await form.getByRole("checkbox", { name: "Include messages from bots" }).check();
  await expect(form.getByText(/matched against the roles of whoever posted or reacted/)).toBeVisible();
  await noSeriousAxe(page, "discord trigger filters");
  await form.getByRole("button", { name: "Create rule" }).click();
  await expect(page).toHaveURL(/\/workflows\?id=discord-to-library/);
  const created = api.calls.filter((c) => c.method === "POST" && c.path === "/rules").map((c) => c.body as any);
  expect(created).toHaveLength(1);
  expect(created[0].trigger).toEqual({
    kind: "event",
    params: { type: "discord.message.created", channels: ["101"], users: ["ori"], include_bots: true },
  });

  // 2. The workflow: a markdown.render step, then a library.push step.
  const add = page.getByRole("button", { name: "Add step" });
  await add.click();
  const card = (name: string) => page.getByRole("group", { name, exact: true });
  await card("New step").locator(".wf-card__name").click();
  await page.getByRole("button", { name: "Edit New step", exact: true }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("Name").fill("Render");
  await dialog.getByLabel("Kind").selectOption("code");
  await dialog.getByLabel("Built-in").selectOption("markdown.render");
  await expect(dialog.getByRole("textbox", { name: "Name of input title" })).toBeVisible();
  await noSeriousAxe(page, "markdown.render step");
  await dialog.getByRole("button", { name: "Done" }).click();

  await add.click();
  await card("New step").locator(".wf-card__name").click();
  await page.getByRole("button", { name: "Edit New step", exact: true }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("Name").fill("Push");
  await dialog.getByLabel("Kind").selectOption("code");
  await dialog.getByText("Configuration").click();
  await dialog.getByRole("button", { name: "Edit config as JSON" }).click();
  await dialog.getByLabel("Config JSON").fill(
    JSON.stringify({
      builtin: "action",
      action: { kind: "library.push", params: { actor: "newspaper-library", kind: "document", format: "md" } },
    }),
  );
  await dialog.getByRole("button", { name: "Done" }).click();

  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => api.calls.filter((c) => c.method === "PUT" && c.path.startsWith("/workflows/")).length).toBe(1);
  const put = api.calls.find((c) => c.method === "PUT" && c.path.startsWith("/workflows/"))!.body as any;
  const [render, push] = put.steps;
  expect(render.kind).toBe("code");
  expect(render.config).toEqual({ builtin: "markdown.render" });
  expect(render.outputs.map((p: any) => p.name)).toEqual(["markdown", "content_base64"]);
  expect(push.config.action.kind).toBe("library.push");
});
