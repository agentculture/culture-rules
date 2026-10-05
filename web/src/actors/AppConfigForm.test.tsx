import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { AppConfigForm } from "./AppConfigForm";
import { appParamsFrom, emptyAppDraft, validateApp, validateHttp, type AppDraft, type FormErrors } from "./app-config";

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

describe("app-config model", () => {
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

  it("refuses a literal credential header in the http policy", () => {
    expect(validateHttp({ allow: "", headers: [{ name: "Authorization", value: "Bearer x" }] })).toHaveProperty("http.0.value");
    expect(validateHttp({ allow: "", headers: [{ name: "Authorization", value: "grant:api" }] })).toEqual({});
  });
});
