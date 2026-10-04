import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Actors from "../routes/Actors";
import * as client from "../api/client";
import { getAgentState, resetAgentState } from "../agent-state/store";
import { MACHINES } from "../fixtures/rules-fixture";
import { ACTORS } from "./actors-fixture";

interface Call {
  method: string;
  path: string;
  body: unknown;
}

/** A stateful fetch stub: GET lists, PUT/POST/DELETE mutate and are recorded. */
function mockActorsApi(overrides: Record<string, { status: number; body: unknown }> = {}) {
  const calls: Call[] = [];
  let actors = structuredClone(ACTORS) as unknown as Record<string, unknown>[];
  const reply = (status: number, body: unknown) =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === "string" ? input : input.toString(), "http://x");
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ method, path: url.pathname, body });
      const key = `${method} ${url.pathname}`;
      if (overrides[key]) return reply(overrides[key].status, overrides[key].body);
      if (url.pathname === "/api/machines") return reply(200, { items: MACHINES });
      if (url.pathname === "/api/actors" && method === "GET") return reply(200, { items: actors });
      const m = url.pathname.match(/^\/api\/actors\/([^/]+)(?:\/(enable|disable))?$/);
      if (m) {
        const found = actors.find((a) => a.id === m[1]);
        if (!found) return reply(404, { error: { code: "not_found", message: "no such actor", errors: [] } });
        if (method === "PUT") {
          Object.assign(found, body);
          return reply(200, found);
        }
        if (method === "DELETE") {
          actors = actors.filter((a) => a.id !== m[1]);
          return reply(200, { id: m[1], deleted: true });
        }
        if (m[2]) {
          found.enabled = m[2] === "enable";
          return reply(200, found);
        }
      }
      if (url.pathname === "/api/actors" && method === "POST") {
        actors.push(body as Record<string, unknown>);
        return reply(201, body);
      }
      return reply(404, { error: { code: "not_found", message: url.pathname, errors: [] } });
    }),
  );
  return { calls };
}

function renderActors(path = "/actors") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/actors" element={<Actors />} />
      </Routes>
    </MemoryRouter>,
  );
}

const row = (name: string) => screen.getByRole("group", { name });

describe("Actors board (Chosen — Actors)", () => {
  beforeEach(() => resetAgentState());
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("lists every actor with kind, machine and an enable switch", async () => {
    mockActorsApi();
    renderActors();
    await screen.findByRole("group", { name: "Codex" });
    expect(screen.getAllByRole("group")).toHaveLength(8);
    expect(within(row("PR Watcher")).getByText("daemon")).toBeInTheDocument();
    expect(within(row("Codex")).getByText("thor")).toBeInTheDocument();
    expect(within(row("GitHub")).getByText("anywhere")).toBeInTheDocument();
    expect(within(row("Reachy")).getByRole("switch", { name: "Reachy enabled" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(within(row("Codex")).getByRole("switch", { name: "Codex enabled" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("paints the machine dot in the machine's color", async () => {
    mockActorsApi();
    renderActors();
    await screen.findByRole("group", { name: "Codex" });
    expect(row("Claude Code").querySelector(".machine-dot")).toHaveAttribute("data-machine-slot", "0");
    expect(row("Codex").querySelector(".machine-dot")).toHaveAttribute("data-machine-slot", "1");
    expect(row("Colleague").querySelector(".machine-dot")).toHaveAttribute("data-machine-slot", "2");
  });

  it("expands the first actor inline: repo config source, harness, model, capabilities, edit, delete", async () => {
    mockActorsApi();
    renderActors();
    const card = await screen.findByRole("group", { name: "Claude Code" });
    expect(within(card).getByRole("button", { name: /Claude Code/, expanded: true })).toBeInTheDocument();
    const source = within(card).getByRole("radiogroup", { name: "Configuration source" });
    expect(within(source).getByRole("radio", { name: "repo" })).toHaveAttribute("aria-checked", "true");
    expect(within(source).getByRole("radio", { name: "db" })).toHaveAttribute("aria-checked", "false");
    expect(within(card).getByText("culture-rules/culture.yaml")).toBeInTheDocument();
    expect(within(card).getByText("claude")).toBeInTheDocument();
    expect(within(card).getByText("opus 4.6")).toBeInTheDocument();
    for (const cap of ["review", "write code", "triage"]) {
      expect(within(card).getByText(cap)).toBeInTheDocument();
    }
    expect(within(card).getByRole("button", { name: "Edit Claude Code" })).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Delete Claude Code" })).toBeInTheDocument();
    // Only the selected actor is expanded.
    expect(within(row("Codex")).queryByRole("radiogroup")).toBeNull();
  });

  it("selects another actor by click and by ?id=, collapsing the previous one", async () => {
    mockActorsApi();
    const user = userEvent.setup();
    const { unmount } = renderActors();
    await screen.findByRole("group", { name: "Codex" });
    await user.click(within(row("Codex")).getByRole("button", { name: /Codex/ }));
    expect(within(row("Codex")).getByRole("radiogroup", { name: "Configuration source" })).toBeInTheDocument();
    expect(within(row("Codex")).getByText("db record")).toBeInTheDocument();
    expect(within(row("Claude Code")).queryByRole("radiogroup")).toBeNull();
    unmount();
    renderActors("/actors?id=ori");
    await screen.findByRole("group", { name: "Ori" });
    expect(within(row("Ori")).getByRole("radiogroup")).toBeInTheDocument();
  });

  it("filters by kind with pressed-state buttons", async () => {
    mockActorsApi();
    const user = userEvent.setup();
    renderActors();
    await screen.findByRole("group", { name: "Codex" });
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Agents" }));
    expect(screen.getByRole("button", { name: "Agents" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("group").map((g) => g.getAttribute("aria-label"))).toEqual([
      "Claude Code",
      "Codex",
      "Colleague",
    ]);
    await user.click(screen.getByRole("button", { name: "Robots" }));
    expect(screen.getAllByRole("group").map((g) => g.getAttribute("aria-label"))).toEqual(["Reachy"]);
    await user.click(screen.getByRole("button", { name: "All" }));
    expect(screen.getAllByRole("group")).toHaveLength(8);
  });

  it("toggles an actor with POST /actors/{id}/disable and /enable", async () => {
    const { calls } = mockActorsApi();
    const user = userEvent.setup();
    renderActors();
    await screen.findByRole("group", { name: "Codex" });
    await user.click(within(row("Codex")).getByRole("switch", { name: "Codex enabled" }));
    await waitFor(() =>
      expect(within(row("Codex")).getByRole("switch")).toHaveAttribute("aria-checked", "false"),
    );
    expect(calls).toContainEqual({ method: "POST", path: "/api/actors/codex/disable", body: undefined });
    await user.click(within(row("Reachy")).getByRole("switch"));
    await waitFor(() =>
      expect(within(row("Reachy")).getByRole("switch")).toHaveAttribute("aria-checked", "true"),
    );
    expect(calls).toContainEqual({ method: "POST", path: "/api/actors/reachy/enable", body: undefined });
  });

  it("edits the selected actor and PUTs the full definition", async () => {
    const { calls } = mockActorsApi();
    const user = userEvent.setup();
    renderActors();
    const card = await screen.findByRole("group", { name: "Claude Code" });
    await user.click(within(card).getByRole("button", { name: "Edit Claude Code" }));
    const model = within(card).getByRole("textbox", { name: "Model" });
    await user.clear(model);
    await user.type(model, "opus 5");
    const caps = within(card).getByRole("textbox", { name: "Capabilities" });
    await user.clear(caps);
    await user.type(caps, "review, triage");
    await user.click(within(card).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(within(row("Claude Code")).getByText("opus 5")).toBeInTheDocument());
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.path).toBe("/api/actors/claude-code");
    expect(put?.body).toMatchObject({
      id: "claude-code",
      name: "Claude Code",
      kind: "agent",
      model: "opus 5",
      harness: "claude",
      capabilities: ["review", "triage"],
      config_source: "repo",
    });
    expect(within(row("Claude Code")).queryByRole("textbox")).toBeNull();
  });

  it("switches the configuration source with a PUT", async () => {
    const { calls } = mockActorsApi();
    const user = userEvent.setup();
    renderActors();
    const card = await screen.findByRole("group", { name: "Claude Code" });
    await user.click(within(card).getByRole("radio", { name: "db" }));
    await waitFor(() =>
      expect(within(row("Claude Code")).getByRole("radio", { name: "db" })).toHaveAttribute(
        "aria-checked",
        "true",
      ),
    );
    expect(calls.find((c) => c.method === "PUT")?.body).toMatchObject({ config_source: "db" });
  });

  it("the configuration source is one tab stop; arrow keys switch it and focus follows", async () => {
    const { calls } = mockActorsApi();
    const user = userEvent.setup();
    renderActors();
    const card = await screen.findByRole("group", { name: "Claude Code" });
    const repo = within(card).getByRole("radio", { name: "repo" });
    const db = within(card).getByRole("radio", { name: "db" });
    expect(repo).toHaveAttribute("tabindex", "0");
    expect(db).toHaveAttribute("tabindex", "-1");
    repo.focus();
    await user.keyboard("{ArrowRight}");
    await waitFor(() => expect(db).toHaveAttribute("aria-checked", "true"));
    expect(db).toHaveFocus();
    expect(db).toHaveAttribute("tabindex", "0");
    expect(calls.find((c) => c.method === "PUT")?.body).toMatchObject({ config_source: "db" });
  });

  it("deletes only after an inline confirmation, with DELETE /actors/{id}", async () => {
    const { calls } = mockActorsApi();
    const user = userEvent.setup();
    renderActors();
    const card = await screen.findByRole("group", { name: "Claude Code" });
    await user.click(within(card).getByRole("button", { name: "Delete Claude Code" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(within(card).getByRole("button", { name: "Cancel" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(within(card).getByRole("button", { name: "Delete Claude Code" }));
    await user.click(within(card).getByRole("button", { name: "Confirm delete" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Claude Code" })).toBeNull());
    expect(calls).toContainEqual({ method: "DELETE", path: "/api/actors/claude-code", body: undefined });
    // The next actor takes the expansion.
    expect(within(row("Codex")).getByRole("radiogroup")).toBeInTheDocument();
  });

  it("adds an actor with POST /actors", async () => {
    const { calls } = mockActorsApi();
    const user = userEvent.setup();
    renderActors();
    await screen.findByRole("group", { name: "Codex" });
    await user.click(screen.getByRole("button", { name: "Add actor" }));
    await user.type(screen.getByRole("textbox", { name: "Id" }), "gemini");
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Gemini");
    await user.selectOptions(screen.getByRole("combobox", { name: "Kind" }), "agent");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByRole("group", { name: "Gemini" })).toBeInTheDocument());
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/actors")?.body).toMatchObject({
      id: "gemini",
      name: "Gemini",
      kind: "agent",
    });
  });

  it("names a failed mutation and leaves the state untouched", async () => {
    mockActorsApi({
      "POST /api/actors/codex/disable": {
        status: 409,
        body: { error: { code: "conflict", message: "actor is in use", errors: [] } },
      },
    });
    const user = userEvent.setup();
    renderActors();
    await screen.findByRole("group", { name: "Codex" });
    await user.click(within(row("Codex")).getByRole("switch"));
    expect(await screen.findByRole("alert")).toHaveTextContent("actor is in use");
    expect(within(row("Codex")).getByRole("switch")).toHaveAttribute("aria-checked", "true");
  });

  it("reports ready in agent-state with the roster it drew", async () => {
    mockActorsApi();
    renderActors();
    await waitFor(() => expect(getAgentState().status).not.toBe("loading"));
    await waitFor(() => expect(getAgentState().view_ready).toBe(true));
    expect(getAgentState().tab).toBe("actors");
    expect(getAgentState()).toMatchObject({
      actors: { count: 8, shown: 8, kind: "all", selected: "claude-code" },
    });
    expect(getAgentState().errors).toEqual([]);
  });

  it("names a load that fails while being applied and still reports ready", async () => {
    mockActorsApi();
    // A rejection reason with no string form: describing it throws inside the load's .then.
    vi.spyOn(client, "listMachines").mockRejectedValue(Object.create(null));
    renderActors();
    expect(await screen.findByRole("alert")).toHaveTextContent(/primitive/i);
    await waitFor(() => expect(getAgentState().view_ready).toBe(true));
  });

  it("names a load failure and still reports ready", async () => {
    mockActorsApi({
      "GET /api/actors": { status: 503, body: { error: { code: "store_down", message: "store unreachable", errors: [] } } },
    });
    renderActors();
    expect(await screen.findByRole("alert")).toHaveTextContent("store unreachable");
    await waitFor(() => expect(getAgentState().view_ready).toBe(true));
    expect(getAgentState().errors).toEqual(["store unreachable"]);
  });
});

describe("Actors toggle in flight (#7)", () => {
  it("a double click while the toggle is in flight sends one request", async () => {
    const { calls } = mockActorsApi();
    const inner = globalThis.fetch;
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    vi.stubGlobal("fetch", (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (/\/actors\/codex\/(enable|disable)$/.test(String(input))) await gate;
      return inner(input, init);
    }) as typeof fetch);
    renderActors();
    await screen.findByRole("group", { name: "Codex" });
    const sw = within(row("Codex")).getByRole("switch", { name: "Codex enabled" });
    act(() => {
      sw.click();
      sw.click();
    });
    await waitFor(() => expect(sw).toHaveAttribute("aria-disabled", "true"));
    release();
    await waitFor(() => expect(sw).not.toHaveAttribute("aria-disabled"));
    expect(calls.filter((c) => /\/actors\/codex\/(enable|disable)$/.test(c.path))).toHaveLength(1);
  });
});

describe("Actors tab: app and runner editors (t38)", () => {
  beforeEach(() => resetAgentState());
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  async function addApp(user: ReturnType<typeof userEvent.setup>) {
    await screen.findByRole("group", { name: "Codex" });
    await user.click(screen.getByRole("button", { name: "Add actor" }));
    await user.type(screen.getByRole("textbox", { name: "Id" }), "gh-app");
    await user.type(screen.getByRole("textbox", { name: "Name" }), "GH App");
    await user.selectOptions(screen.getByRole("combobox", { name: "Kind" }), "app");
    await user.selectOptions(screen.getByRole("combobox", { name: "Surface" }), "github");
  }

  it("refuses a literal secret client-side: no request is sent", async () => {
    const { calls } = mockActorsApi();
    const user = userEvent.setup();
    renderActors();
    await addApp(user);
    await user.type(screen.getByRole("textbox", { name: "Private key (grant reference)" }), "-----BEGIN RSA PRIVATE KEY");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Secrets are never typed here/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Private key (grant reference)" })).toHaveAttribute("aria-invalid", "true");
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/actors")).toBe(false);
  });

  it("shows a server secret_literal refusal as guided text, never the raw message", async () => {
    const { calls } = mockActorsApi({
      "POST /api/actors": {
        status: 422,
        body: { error: { code: "secret_literal", message: "params.connection.private_key: literal RAW-SERVER-TEXT", errors: [] } },
      },
    });
    const user = userEvent.setup();
    renderActors();
    await addApp(user);
    await user.type(screen.getByRole("textbox", { name: "Private key (grant reference)" }), "grant:gh-key");
    await user.click(screen.getByRole("button", { name: "Save" }));
    const alert = await screen.findByText(/Reference a stored secret instead/);
    expect(alert).toBeInTheDocument();
    expect(screen.queryByText(/RAW-SERVER-TEXT/)).toBeNull();
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/actors")).toBe(true);
  });

  it("saves an app actor's connection and declarations under params", async () => {
    const { calls } = mockActorsApi();
    const user = userEvent.setup();
    renderActors();
    await addApp(user);
    await user.type(screen.getByRole("textbox", { name: "App id" }), "42");
    await user.type(screen.getByRole("textbox", { name: "Private key (grant reference)" }), "grant:gh-key");
    await user.type(screen.getByRole("textbox", { name: "Repositories" }), "agentculture/culture-rules");
    await user.click(screen.getByRole("button", { name: "Add event" }));
    await user.type(screen.getByRole("textbox", { name: "Event 1" }), "github.pr.opened");
    await user.click(screen.getByRole("checkbox", { name: /^github\.comment/ }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByRole("group", { name: "GH App" })).toBeInTheDocument());
    const post = calls.find((c) => c.method === "POST" && c.path === "/api/actors");
    expect(post?.body).toMatchObject({
      id: "gh-app",
      kind: "app",
      params: {
        surface: "github",
        connection: { app_id: "42", private_key: "grant:gh-key", repos: ["agentculture/culture-rules"] },
        events: ["github.pr.opened"],
        actions: ["github.comment"],
      },
    });
  });

  it("a runner command saved from the form round-trips through PUT /actors/{id}", async () => {
    const { calls } = mockActorsApi();
    const user = userEvent.setup();
    const first = renderActors("/actors?id=thor-runner");
    const card = await screen.findByRole("group", { name: "thor runner" });
    await user.click(within(card).getByRole("button", { name: "Edit thor runner" }));
    await user.click(within(card).getByRole("button", { name: "Add command" }));
    await user.type(within(card).getByRole("textbox", { name: "Command 1 name" }), "echo");
    await user.type(within(card).getByRole("textbox", { name: "Command 1 token 1" }), "echo");
    await user.click(within(card).getByRole("button", { name: "Add token to command 1" }));
    await user.type(within(card).getByRole("textbox", { name: "Command 1 token 2" }), "{{msg}");
    await user.click(within(card).getByRole("button", { name: "Add parameter to command 1" }));
    await user.type(within(card).getByRole("textbox", { name: "Command 1 parameter 1 name" }), "msg");
    await user.type(within(card).getByRole("textbox", { name: "Command 1 timeout" }), "30");
    await user.click(within(card).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.path).toBe("/api/actors/thor-runner");
    expect(put?.body).toMatchObject({
      params: { commands: { echo: { argv: ["echo", "{msg}"], params: { msg: "string" }, timeout: 30 } } },
    });
    // Reload: a fresh render of the same stored actor shows the same command in the editor.
    first.unmount();
    renderActors("/actors?id=thor-runner");
    const again = await screen.findByRole("group", { name: "thor runner" });
    await user.click(within(again).getByRole("button", { name: "Edit thor runner" }));
    expect(within(again).getByRole("textbox", { name: "Command 1 name" })).toHaveValue("echo");
    expect(within(again).getByRole("textbox", { name: "Command 1 token 1" })).toHaveValue("echo");
    expect(within(again).getByRole("textbox", { name: "Command 1 token 2" })).toHaveValue("{msg}");
    expect(within(again).getByRole("textbox", { name: "Command 1 parameter 1 name" })).toHaveValue("msg");
    expect(within(again).getByRole("combobox", { name: "Command 1 parameter 1 type" })).toHaveValue("string");
    expect(within(again).getByRole("textbox", { name: "Command 1 timeout" })).toHaveValue("30");
  });

  it("does not send a runner command whose placeholder has no declared parameter", async () => {
    const { calls } = mockActorsApi();
    const user = userEvent.setup();
    renderActors("/actors?id=thor-runner");
    const card = await screen.findByRole("group", { name: "thor runner" });
    await user.click(within(card).getByRole("button", { name: "Edit thor runner" }));
    await user.click(within(card).getByRole("button", { name: "Add command" }));
    await user.type(within(card).getByRole("textbox", { name: "Command 1 name" }), "echo");
    await user.type(within(card).getByRole("textbox", { name: "Command 1 token 1" }), "echo {{msg}");
    await user.click(within(card).getByRole("button", { name: "Save" }));
    expect(await within(card).findByText(/Declare a parameter for \{msg\}/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });
});
