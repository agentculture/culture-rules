import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Actors from "../routes/Actors";
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
  afterEach(() => vi.unstubAllGlobals());

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
