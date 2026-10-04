import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./fixtures/api";
import { mockActorsApi } from "./fixtures/actors";

/**
 * The typed actor forms: an app actor's connection (grant references only,
 * a literal secret is refused) and declarations, and a runner's commands.
 */
async function open(page: Page, path = "/actors") {
  await mockApi(page);
  const calls = await mockActorsApi(page);
  await page.goto(path);
  await expect
    .poll(async () => JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}").status)
    .toBe("ready");
  return calls;
}

async function noSeriousAxe(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${label}: ${v.id} — ${v.help} (${v.nodes.map((n) => n.target).join(" ")})`)).toEqual([]);
}

test.describe("Actors tab: typed configuration", () => {
  test("a new app actor takes a grant reference; a literal secret is refused", async ({ page }) => {
    const calls = await open(page);
    await page.getByRole("button", { name: "Add actor" }).click();
    const form = page.getByRole("form", { name: "Add actor" });
    await form.getByRole("textbox", { name: "Id" }).fill("github-app");
    await form.getByRole("textbox", { name: "Name" }).fill("GitHub app");
    await form.getByRole("combobox", { name: "Kind" }).selectOption("app");

    // The surface comes first; connection and declarations appear after it.
    await expect(form.getByRole("group", { name: "Connection" })).toHaveCount(0);
    await form.getByRole("combobox", { name: "Surface" }).selectOption("github");
    await expect(form.getByRole("group", { name: "Connection" })).toBeVisible();
    await expect(form.getByRole("group", { name: "Declarations" })).toBeVisible();

    const key = form.getByRole("textbox", { name: "Private key (grant reference)" });
    await expect(key).toHaveAttribute("placeholder", "grant:NAME");
    await form.getByRole("textbox", { name: "App id" }).fill("12");
    await key.fill(["-----BEGIN", "PRIVATE", "KEY-----"].join(" "));
    await form.getByRole("button", { name: "Add event" }).click();
    await form.getByRole("textbox", { name: "Event 1" }).fill("github.pr.opened");
    await form.getByRole("checkbox", { name: /^github\.comment/ }).check();
    await noSeriousAxe(page, "add app actor form");

    await form.getByRole("button", { name: "Save" }).click();
    await expect(key).toHaveAttribute("aria-invalid", "true");
    await expect(key).toHaveAccessibleDescription(/\S/);
    expect(calls.filter((c) => c.method === "POST" && c.path === "/api/actors")).toHaveLength(0);
    await noSeriousAxe(page, "add app actor form with a refused secret");

    await key.fill("grant:gh-key");
    await form.getByRole("button", { name: "Save" }).click();
    await expect(page.getByRole("form", { name: "Add actor" })).toHaveCount(0);
    const post = calls.find((c) => c.method === "POST" && c.path === "/api/actors");
    expect(post?.body).toMatchObject({
      id: "github-app",
      kind: "app",
      params: {
        surface: "github",
        connection: { app_id: "12", private_key: "grant:gh-key" },
        events: ["github.pr.opened"],
        actions: ["github.comment"],
      },
    });
    expect(JSON.stringify(post?.body)).not.toContain("PRIVATE KEY");
  });

  test("a runner's command is added with argv, a typed param and a timeout, and PUT as params.commands", async ({ page }) => {
    const calls = await open(page, "/actors?id=thor-runner");
    const card = page.getByRole("group", { name: "thor runner" });
    await card.getByRole("button", { name: "Edit thor runner" }).click();
    const form = card.getByRole("form", { name: "Edit thor runner" });
    await form.getByRole("button", { name: "Add command" }).click();
    const cmd = form.getByRole("group", { name: "Command 1" });
    await cmd.getByRole("textbox", { name: "Command 1 name" }).fill("echo");
    await cmd.getByRole("textbox", { name: "Command 1 token 1" }).fill("echo");
    await cmd.getByRole("button", { name: "Add token to command 1" }).click();
    await cmd.getByRole("textbox", { name: "Command 1 token 2" }).fill("{msg}");
    await cmd.getByRole("button", { name: "Add parameter to command 1" }).click();
    await cmd.getByRole("textbox", { name: "Command 1 parameter 1 name" }).fill("msg");
    await cmd.getByRole("combobox", { name: "Command 1 parameter 1 type" }).selectOption("string");
    await cmd.getByRole("textbox", { name: "Command 1 timeout" }).fill("30");
    await noSeriousAxe(page, "edit runner commands");

    await form.getByRole("button", { name: "Save" }).click();
    await expect(card.getByRole("form")).toHaveCount(0);
    const put = calls.find((c) => c.method === "PUT" && c.path === "/api/actors/thor-runner");
    expect((put?.body as { params: unknown }).params).toMatchObject({
      commands: { echo: { argv: ["echo", "{msg}"], params: { msg: "string" }, timeout: 30 } },
    });
  });

  test("an undeclared placeholder is flagged on the command and nothing is sent", async ({ page }) => {
    const calls = await open(page, "/actors?id=thor-runner");
    const card = page.getByRole("group", { name: "thor runner" });
    await card.getByRole("button", { name: "Edit thor runner" }).click();
    const form = card.getByRole("form", { name: "Edit thor runner" });
    await form.getByRole("button", { name: "Add command" }).click();
    await form.getByRole("textbox", { name: "Command 1 name" }).fill("bad");
    await form.getByRole("textbox", { name: "Command 1 token 1" }).fill("{x}");
    await form.getByRole("button", { name: "Save" }).click();
    await expect(form.getByRole("textbox", { name: "Command 1 token 1" })).toHaveAttribute("aria-invalid", "true");
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
    await noSeriousAxe(page, "runner command with an error");
  });
});
