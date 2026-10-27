import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { AppConfigForm } from "./AppConfigForm";
import { appDraftFrom, appParamsFrom, emptyAppDraft, validateApp, validateHttp, type AppDraft, type FormErrors } from "./app-config";

let latest: AppDraft = emptyAppDraft();

function Harness({ initial = emptyAppDraft(), errors }: Readonly<{ initial?: AppDraft; errors?: FormErrors }>) {
  const [draft, setDraft] = useState(initial);
  latest = draft;
  return <AppConfigForm value={draft} onChange={setDraft} errors={errors} />;
}

describe("AppConfigForm", () => {
  it("asks for the surface first, then reveals connection and declarations", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(screen.getByRole("combobox", { name: "Surface" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Connection" })).toBeNull();
    expect(screen.queryByRole("group", { name: "Declarations" })).toBeNull();
    await user.selectOptions(screen.getByRole("combobox", { name: "Surface" }), "github");
    expect(screen.getByRole("group", { name: "Connection" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Declarations" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "App id" })).toBeInTheDocument();
  });

  it("offers secret fields as grant references with a hint, never a plain secret input", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Surface" }), "jira");
    const token = screen.getByRole("textbox", { name: "Token (grant reference)" });
    expect(token).toHaveAttribute("placeholder", "grant:NAME");
    expect(token).toHaveAccessibleDescription(/stored secret/i);
    expect(screen.getByRole("textbox", { name: "Webhook token (grant reference)" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Site" })).toBeInTheDocument();
  });

  it("shows a field error on the field it belongs to", async () => {
    render(
      <Harness
        initial={{ ...emptyAppDraft(), surface: "discord", connection: { bot_token: "abc" } }}
        errors={{ "connection.bot_token": "Secrets are never typed here." }}
      />,
    );
    const field = screen.getByRole("textbox", { name: "Bot token (grant reference)" });
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(field).toHaveAccessibleDescription(/never typed here/);
  });

  it("adds and removes declared events, each validated with the dotted rule", async () => {
    const user = userEvent.setup();
    render(<Harness initial={{ ...emptyAppDraft(), surface: "github" }} />);
    await user.click(screen.getByRole("button", { name: "Add event" }));
    await user.type(screen.getByRole("textbox", { name: "Event 1" }), "github.pr.opened");
    expect(latest.events).toEqual(["github.pr.opened"]);
    await user.click(screen.getByRole("button", { name: "Add event" }));
    await user.type(screen.getByRole("textbox", { name: "Event 2" }), "Opened");
    expect(validateApp(latest)).toHaveProperty("events.1");
    expect(validateApp(latest)).not.toHaveProperty("events.0");
    await user.click(screen.getByRole("button", { name: "Remove event 2" }));
    expect(latest.events).toEqual(["github.pr.opened"]);
  });

  it("offers the catalogued action kinds as toggles", async () => {
    const user = userEvent.setup();
    render(<Harness initial={{ ...emptyAppDraft(), surface: "github" }} />);
    for (const kind of [
      "noop",
      "message",
      "discord.message",
      "github.comment",
      "jira.comment",
      "http.call",
      "machine.command",
      "email.send",
      "search.web",
      "llm.complete",
      "library.push",
    ]) {
      expect(screen.getByRole("checkbox", { name: new RegExp(`^${kind.replace(".", "\\.")}`) })).toBeInTheDocument();
    }
    await user.click(screen.getByRole("checkbox", { name: /^github\.comment/ }));
    expect(latest.actions).toEqual(["github.comment"]);
  });

  it("edits probes and the self identity", async () => {
    const user = userEvent.setup();
    render(<Harness initial={{ ...emptyAppDraft(), surface: "github" }} />);
    await user.click(screen.getByRole("button", { name: "Add probe" }));
    await user.type(screen.getByRole("textbox", { name: "Probe 1 name" }), "ci");
    await user.type(screen.getByRole("textbox", { name: "Probe 1 command" }), "gh run list");
    await user.type(screen.getByRole("textbox", { name: "Self identity" }), "culture-bot");
    expect(latest.probes).toEqual([{ name: "ci", command: "gh run list", schedule: "" }]);
    expect(latest.selfIdentity).toBe("culture-bot");
  });
});

describe("AppConfigForm: email and search", () => {
  it("offers the email and search surfaces", () => {
    render(<Harness />);
    const options = within(screen.getByRole("combobox", { name: "Surface" }))
      .getAllByRole("option")
      .map((o) => o.getAttribute("value"));
    expect(options).toEqual(["", "github", "jira", "discord", "email", "search"]);
  });

  it("shows the email connection with the sender domain as a drop-down of exactly two domains", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Surface" }), "email");
    expect(screen.getByRole("textbox", { name: "API key (grant reference)" })).toHaveAttribute("placeholder", "grant:NAME");
    expect(screen.getByRole("textbox", { name: "Sender local part" })).toHaveAttribute("placeholder", "no-reply");
    expect(screen.getByRole("textbox", { name: "Recipients" })).toBeInTheDocument();
    const domain = screen.getByRole("combobox", { name: "Sender domain" });
    expect(within(domain).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual([
      "culture.dev",
      "jetson-ai-lab.org",
    ]);
    expect(domain).toHaveValue("culture.dev");
    expect(latest.connection.domain).toBe("culture.dev");
    await user.selectOptions(domain, "jetson-ai-lab.org");
    expect(latest.connection.domain).toBe("jetson-ai-lab.org");
  });

  it("shows the search connection as one API key reference", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Surface" }), "search");
    expect(screen.getByRole("textbox", { name: "API key (grant reference)" })).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Sender domain" })).toBeNull();
  });
});

describe("app-config model", () => {
  it("validates the email connection: key reference required, a known domain, addresses only", () => {
    const base = { ...emptyAppDraft(), surface: "email" as const };
    expect(
      validateApp({ ...base, connection: { api_key: "grant:resend", domain: "culture.dev", recipients: "a@culture.dev" } }),
    ).toEqual({});
    expect(validateApp({ ...base, connection: {} })).toHaveProperty("connection.api_key");
    const bad = validateApp({
      ...base,
      connection: { api_key: "re_123", domain: "example.com", recipients: "a@culture.dev, nobody" },
    });
    expect(Object.keys(bad).sort()).toEqual(["connection.api_key", "connection.domain", "connection.recipients"]);
  });

  it("requires the search key as a reference", () => {
    const base = { ...emptyAppDraft(), surface: "search" as const };
    expect(validateApp({ ...base, connection: { api_key: "grant:brave" } })).toEqual({});
    expect(validateApp({ ...base, connection: {} })).toHaveProperty("connection.api_key");
  });

  it("writes an email connection with recipients as a list and keeps an empty allow-list", () => {
    const draft: AppDraft = {
      ...emptyAppDraft(),
      surface: "email",
      connection: { api_key: "grant:resend", domain: "jetson-ai-lab.org", recipients: "a@x.org, b@y.org" },
      actions: ["email.send"],
    };
    expect(appParamsFrom(draft, undefined).connection).toEqual({
      api_key: "grant:resend",
      domain: "jetson-ai-lab.org",
      recipients: ["a@x.org", "b@y.org"],
    });
    const empty = appParamsFrom({ ...draft, connection: { api_key: "grant:resend", recipients: "" } }, undefined);
    expect(empty.connection).toEqual({ api_key: "grant:resend", recipients: [] });
  });

  it("accepts grant refs and refuses literals on every secret-looking key", () => {
    const base = { ...emptyAppDraft(), surface: "github" as const };
    expect(validateApp({ ...base, connection: { private_key: "grant:gh-key", webhook_secret: "grant:wh" } })).toEqual({});
    const bad = validateApp({ ...base, connection: { private_key: "-----BEGIN KEY", webhook_secret: "s3cr3t" } });
    expect(Object.keys(bad).sort()).toEqual(["connection.private_key", "connection.webhook_secret"]);
  });

  it("writes lists as arrays and omits blank connection fields", () => {
    const draft: AppDraft = {
      ...emptyAppDraft(),
      surface: "github",
      connection: { app_id: "12", repos: "a/b, c/d", private_key: "grant:k", installation_id: "" },
      events: ["github.pr.opened"],
      actions: ["github.comment"],
    };
    expect(appParamsFrom(draft, undefined)).toEqual({
      surface: "github",
      connection: { app_id: "12", repos: ["a/b", "c/d"], private_key: "grant:k" },
      events: ["github.pr.opened"],
      actions: ["github.comment"],
    });
  });

  it("writes one Discord guild id as a string and several as a list", () => {
    const draft: AppDraft = {
      ...emptyAppDraft(),
      surface: "discord",
      connection: { bot_token: "grant:bot", guild_id: " 10 " },
      events: ["discord.message.created"],
    };
    expect(appParamsFrom(draft, undefined).connection).toEqual({ bot_token: "grant:bot", guild_id: "10" });
    const several = appParamsFrom({ ...draft, connection: { bot_token: "grant:bot", guild_id: "10, 20, 10" } }, undefined);
    expect(several.connection).toEqual({ bot_token: "grant:bot", guild_id: ["10", "20"] });
    const unset = appParamsFrom({ ...draft, connection: { bot_token: "grant:bot", guild_id: " , " } }, undefined);
    expect(unset.connection).toEqual({ bot_token: "grant:bot" });
  });

  it("reads a Discord guild id stored as a string or a list", () => {
    const base = { surface: "discord" as const, events: [], actions: [] };
    expect(appDraftFrom({ ...base, connection: { guild_id: "10" } }).connection.guild_id).toBe("10");
    expect(appDraftFrom({ ...base, connection: { guild_id: ["10", "20"] } }).connection.guild_id).toBe("10, 20");
  });

  it("hints that the Discord guild field takes several ids", () => {
    render(<Harness initial={{ ...emptyAppDraft(), surface: "discord" }} />);
    expect(screen.getByRole("textbox", { name: "Guild ids" })).toHaveAccessibleDescription(/separate with commas/i);
  });

  it("saves the Discord command guilds as one id or a list", () => {
    const draft: AppDraft = {
      ...emptyAppDraft(),
      surface: "discord",
      connection: { bot_token: "grant:bot", guild_id: "10, 20", command_guild_id: "20" },
      events: ["discord.command.invoked"],
    };
    expect(appParamsFrom(draft, undefined).connection).toEqual({
      bot_token: "grant:bot",
      guild_id: ["10", "20"],
      command_guild_id: "20",
    });
  });

  it("refuses command guild ids that are not ids or not among the guild ids", () => {
    const base = { ...emptyAppDraft(), surface: "discord" as const, events: [] };
    const conn = (command_guild_id: string, guild_id = "10, 20") => ({ bot_token: "grant:bot", guild_id, command_guild_id });
    expect(validateApp({ ...base, connection: conn("20") })).toEqual({});
    expect(validateApp({ ...base, connection: conn("") })).toEqual({});
    expect(validateApp({ ...base, connection: conn("30") })).toHaveProperty("connection.command_guild_id");
    expect(validateApp({ ...base, connection: conn("my server") })).toHaveProperty("connection.command_guild_id");
    expect(validateApp({ ...base, connection: conn("30", "") })).toEqual({}); // every guild: any id
  });

  it("offers the Discord command guilds field", () => {
    render(<Harness initial={{ ...emptyAppDraft(), surface: "discord" }} />);
    expect(screen.getByRole("textbox", { name: "Command guild ids (unset: all guild ids)" })).toHaveAccessibleDescription(
      /separate with commas/i,
    );
  });

  it("refuses a literal credential header in the http policy", () => {
    expect(validateHttp({ allow: "", headers: [{ name: "Authorization", value: "Bearer x" }] })).toHaveProperty("http.0.value");
    expect(validateHttp({ allow: "", headers: [{ name: "Authorization", value: "grant:api" }] })).toEqual({});
  });
});
