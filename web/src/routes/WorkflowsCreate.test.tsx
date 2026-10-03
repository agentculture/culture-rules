import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Workflows from "./Workflows";
import { getAgentState, resetAgentState } from "../agent-state/store";
import { workflowsState } from "../workflows/agentState";
import type { WorkflowDef } from "../api/workflows";
import { MACHINES, WHOAMI } from "../fixtures/rules-fixture";
import { ACTORS, REPOS, WORKFLOW_DOCS, WORKFLOW_RULES } from "../workflows/fixture";
import { DOMMatrixStub, MeasuringResizeObserver, useMeasuredLayout } from "../test/reactFlow";

useMeasuredLayout();

/** A workflow no rule uses, so the API lets it be deleted. */
const SPARE: WorkflowDef = { id: "nightly-report", name: "Nightly report", version: 1, steps: [] };

interface Call {
  method: string;
  path: string;
  body: unknown;
}

/**
 * A stateful fake of the workflow routes (api/openapi.json): list, create
 * (409 on a taken id, deleted ones included), enable/disable, soft delete
 * (409 while a rule uses it) and restore. Everything else answers a fixture.
 */
function fakeApi(start: WorkflowDef[], opts: { deleted?: string[]; createError?: unknown } = {}) {
  const calls: Call[] = [];
  let live = start.map((w) => ({ ...w }));
  const gone = new Map<string, WorkflowDef>((opts.deleted ?? []).map((id) => [id, { id, name: id }]));
  const used = new Set(WORKFLOW_RULES.map((r) => r.workflow?.id).filter(Boolean));
  const json = (status: number, data: unknown) =>
    new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
  const fail = (status: number, code: string, message: string) =>
    json(status, { error: { code, message, errors: [] } });

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).split("?")[0];
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(init.body as string) : null;
    calls.push({ method, path, body });
    if (path === "/api/workflows" && method === "GET") return json(200, { items: live });
    if (path === "/api/workflows" && method === "POST") {
      if (opts.createError) return json(422, opts.createError);
      if (live.some((w) => w.id === body.id) || gone.has(body.id)) {
        return fail(409, "conflict", `workflows/${body.id} already exists`);
      }
      const stored = { ...body, version: 1, schema_version: "1.0", created_at: "2026-10-03T12:00:00Z" };
      live = [...live, stored];
      return json(201, stored);
    }
    const toggle = path.match(/^\/api\/workflows\/([^/]+)\/(enable|disable)$/);
    if (toggle && method === "POST") {
      live = live.map((w) => (w.id === toggle[1] ? { ...w, enabled: toggle[2] === "enable" } : w));
      return json(200, live.find((w) => w.id === toggle[1]));
    }
    const restore = path.match(/^\/api\/workflows\/([^/]+)\/restore$/);
    if (restore && method === "POST") {
      const doc = gone.get(restore[1]);
      if (!doc) return fail(404, "not_found", "nothing to restore");
      gone.delete(restore[1]);
      live = [...live, doc];
      return json(200, doc);
    }
    const one = path.match(/^\/api\/workflows\/([^/]+)$/);
    if (one && method === "DELETE") {
      if (used.has(one[1])) return fail(409, "conflict", `workflows/${one[1]} is used by a rule`);
      const doc = live.find((w) => w.id === one[1]);
      if (!doc) return fail(404, "not_found", "no such workflow");
      live = live.filter((w) => w.id !== one[1]);
      gone.set(one[1], doc);
      return json(200, { ...doc, deleted_at: "2026-10-03T12:00:00Z" });
    }
    if (one && method === "PUT") {
      live = live.map((w) => (w.id === one[1] ? body : w));
      return json(200, body);
    }
    const fixed: Record<string, unknown> = {
      "/api/whoami": WHOAMI,
      "/api/rules": { items: WORKFLOW_RULES },
      "/api/machines": { items: MACHINES },
      "/api/actors": { items: ACTORS },
      "/api/runs": { items: [] },
      "/api/repos": { items: REPOS },
    };
    return path in fixed ? json(200, fixed[path]) : fail(404, "not_found", `no route ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, writes: () => calls.filter((c) => c.method !== "GET") };
}

let where = "";
function Where() {
  const loc = useLocation();
  where = `${loc.pathname}${loc.search}`;
  return null;
}

function renderWorkflows(path = "/workflows") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/workflows"
          element={
            <>
              <Workflows />
              <Where />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

const ready = () => waitFor(() => expect(getAgentState().status).toBe("ready"));
const emptyState = () => screen.getByRole("region", { name: "No workflows yet" });
const headerNew = () => screen.getByRole("button", { name: "New workflow", expanded: false });
const nameForm = () => screen.getByRole("form", { name: "New workflow" });

beforeEach(() => {
  resetAgentState();
  vi.stubGlobal("ResizeObserver", MeasuringResizeObserver);
  vi.stubGlobal("DOMMatrixReadOnly", DOMMatrixStub);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Workflows tab: New workflow (empty state)", () => {
  it("offers New workflow in the header and as the empty state's primary action", async () => {
    fakeApi([]);
    renderWorkflows();
    expect(await screen.findByRole("heading", { level: 1, name: "No workflows yet" })).toBeInTheDocument();
    await ready();
    const cta = within(emptyState()).getByRole("button", { name: "New workflow" });
    expect(cta).toHaveClass("wf-button--primary");
    // The header button sits in the io group, next to Import.
    const io = screen.getByRole("button", { name: "Import" }).closest(".wf-head__end")!;
    expect(within(io as HTMLElement).getByRole("button", { name: "New workflow" })).toBeInTheDocument();
    expect(workflowsState()).toMatchObject({ count: 0, selected: null, steps: [], dirty: false });
  });

  it("asks only for a name, focuses it, and Escape closes the form", async () => {
    const user = userEvent.setup();
    const api = fakeApi([]);
    renderWorkflows();
    await ready();
    await user.click(within(emptyState()).getByRole("button", { name: "New workflow" }));
    const form = nameForm();
    expect(within(form).getAllByRole("textbox")).toHaveLength(1);
    expect(within(form).queryByRole("combobox")).toBeNull();
    expect(within(form).getByRole("textbox", { name: "Name" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("form", { name: "New workflow" })).toBeNull();
    expect(within(emptyState()).getByRole("button", { name: "New workflow" })).toBeInTheDocument();
    expect(api.writes()).toEqual([]);
  });

  it("Enter creates a minimal workflow via POST /workflows and opens it on the canvas", async () => {
    const user = userEvent.setup();
    const api = fakeApi([]);
    renderWorkflows();
    await ready();
    await user.click(within(emptyState()).getByRole("button", { name: "New workflow" }));
    await user.type(within(nameForm()).getByRole("textbox", { name: "Name" }), "Triage issues{Enter}");

    expect(await screen.findByRole("heading", { level: 1, name: "Triage issues" })).toBeInTheDocument();
    const posts = api.calls.filter((c) => c.method === "POST" && c.path === "/api/workflows");
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toEqual({
      id: "triage-issues",
      name: "Triage issues",
      inputs: [],
      variables: [],
      steps: [],
      edges: [],
      outputs: [],
      enabled: true,
    });
    await waitFor(() => expect(where).toBe("/workflows?id=triage-issues"));
    expect(screen.getByText("v1")).toBeInTheDocument();
    // Straight into editing: the step + has focus and adds the first step.
    const add = screen.getByRole("button", { name: "Add step" });
    await waitFor(() => expect(add).toHaveFocus());
    await user.click(add);
    await waitFor(() => expect(workflowsState()).toMatchObject({ selected: "triage-issues", steps: ["step-1"], dirty: true }));
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });

  it("shows an API validation error inline and keeps the form open", async () => {
    const user = userEvent.setup();
    fakeApi([], {
      createError: {
        error: { code: "invalid_workflow", message: "name: must be at most 80 characters", errors: [] },
      },
    });
    renderWorkflows();
    await ready();
    await user.click(headerNew());
    await user.type(within(nameForm()).getByRole("textbox", { name: "Name" }), "Too long{Enter}");
    const alert = await within(nameForm()).findByRole("alert");
    expect(alert).toHaveTextContent("name: must be at most 80 characters");
    expect(within(nameForm()).getByRole("textbox", { name: "Name" })).toHaveValue("Too long");
    expect(getAgentState().status).toBe("ready");
  });

  it("a blank name posts nothing", async () => {
    const user = userEvent.setup();
    const api = fakeApi([]);
    renderWorkflows();
    await ready();
    await user.click(headerNew());
    await user.type(within(nameForm()).getByRole("textbox", { name: "Name" }), "   {Enter}");
    expect(api.writes()).toEqual([]);
    expect(nameForm()).toBeInTheDocument();
  });

  it("an id held by a deleted workflow (409) moves on to the next free id", async () => {
    const user = userEvent.setup();
    const api = fakeApi([], { deleted: ["triage"] });
    renderWorkflows();
    await ready();
    await user.click(headerNew());
    await user.type(within(nameForm()).getByRole("textbox", { name: "Name" }), "Triage{Enter}");
    expect(await screen.findByRole("heading", { level: 1, name: "Triage" })).toBeInTheDocument();
    const ids = api.calls
      .filter((c) => c.method === "POST" && c.path === "/api/workflows")
      .map((c) => (c.body as { id: string }).id);
    expect(ids).toEqual(["triage", "triage-2"]);
    expect(where).toBe("/workflows?id=triage-2");
  });
});

describe("Workflows tab: New workflow with a workflow selected", () => {
  it("the header button switches to the new workflow", async () => {
    const user = userEvent.setup();
    fakeApi(WORKFLOW_DOCS);
    renderWorkflows("/workflows?id=review-pr");
    expect(await screen.findByRole("heading", { level: 1, name: "Review PR" })).toBeInTheDocument();
    await ready();
    expect(screen.queryByRole("region", { name: "No workflows yet" })).toBeNull();
    await user.click(headerNew());
    // While naming, the board heads the new workflow; Run and the canvas step aside.
    expect(screen.getByRole("heading", { level: 1, name: "New workflow" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    expect(screen.getByRole("button", { name: "New workflow", expanded: true })).toBeInTheDocument();
    await user.type(within(nameForm()).getByRole("textbox", { name: "Name" }), "Review PR{Enter}");
    // "review-pr" is taken: the slug moves on.
    await waitFor(() => expect(where).toBe("/workflows?id=review-pr-2"));
    expect(await screen.findByRole("heading", { level: 1, name: "Review PR" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Workflow" })).toHaveValue("review-pr-2");
    await waitFor(() => expect(workflowsState()).toMatchObject({ count: 3, selected: "review-pr-2", steps: [] }));
  });

  it("Cancel returns to the selected workflow", async () => {
    const user = userEvent.setup();
    fakeApi(WORKFLOW_DOCS);
    renderWorkflows("/workflows?id=review-pr");
    await ready();
    await user.click(headerNew());
    await user.click(within(nameForm()).getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("heading", { level: 1, name: "Review PR" })).toBeInTheDocument();
    expect(headerNew()).toHaveFocus();
  });
});

describe("Workflows tab: rename (update)", () => {
  it("renames the draft; Save PUTs the new name", async () => {
    const user = userEvent.setup();
    const api = fakeApi(WORKFLOW_DOCS);
    renderWorkflows("/workflows?id=build-image");
    await screen.findByRole("heading", { level: 1, name: "Build image" });
    await ready();
    await user.click(screen.getByRole("button", { name: "Rename workflow" }));
    const form = screen.getByRole("form", { name: "Rename workflow" });
    const field = within(form).getByRole("textbox", { name: "Name" });
    expect(field).toHaveValue("Build image");
    expect(field).toHaveFocus();
    await user.clear(field);
    await user.type(field, "Build and push image{Enter}");
    expect(screen.queryByRole("form", { name: "Rename workflow" })).toBeNull();
    expect(screen.getByRole("heading", { level: 1, name: "Build and push image" })).toBeInTheDocument();
    expect(workflowsState()?.dirty).toBe(true);
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.writes()).toHaveLength(1));
    expect(api.writes()[0]).toMatchObject({
      method: "PUT",
      path: "/api/workflows/build-image",
      body: { id: "build-image", name: "Build and push image" },
    });
  });

  it("Escape leaves the name alone and returns focus to Rename", async () => {
    const user = userEvent.setup();
    const api = fakeApi(WORKFLOW_DOCS);
    renderWorkflows("/workflows?id=build-image");
    await ready();
    const rename = await screen.findByRole("button", { name: "Rename workflow" });
    await user.click(rename);
    await user.keyboard("Other{Escape}");
    expect(screen.queryByRole("form", { name: "Rename workflow" })).toBeNull();
    expect(screen.getByRole("heading", { level: 1, name: "Build image" })).toBeInTheDocument();
    await waitFor(() => expect(rename).toHaveFocus());
    expect(workflowsState()?.dirty).toBe(false);
    expect(api.writes()).toEqual([]);
  });
});

describe("Workflows tab: enable / disable and delete (parity with Rules)", () => {
  it("the workflow switch posts disable then enable", async () => {
    const user = userEvent.setup();
    const api = fakeApi(WORKFLOW_DOCS);
    renderWorkflows("/workflows?id=build-image");
    await screen.findByRole("heading", { level: 1, name: "Build image" });
    await ready();
    const toggle = screen.getByRole("switch", { name: "Workflow enabled" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await user.click(toggle);
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "false"));
    await user.click(toggle);
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    expect(api.writes().map((c) => `${c.method} ${c.path}`)).toEqual([
      "POST /api/workflows/build-image/disable",
      "POST /api/workflows/build-image/enable",
    ]);
    expect(workflowsState()?.dirty).toBe(false);
  });

  it("Delete soft-deletes, moves on, and Undo restores", async () => {
    const user = userEvent.setup();
    const api = fakeApi([...WORKFLOW_DOCS, SPARE]);
    renderWorkflows("/workflows?id=nightly-report");
    await screen.findByRole("heading", { level: 1, name: "Nightly report" });
    await ready();
    await user.click(screen.getByRole("button", { name: "Delete workflow" }));
    expect(await screen.findByText("Deleted Nightly report")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { level: 1, name: "Build image" })).toBeInTheDocument();
    await waitFor(() => expect(where).toBe("/workflows?id=build-image"));
    expect(screen.getByRole("combobox", { name: "Workflow" })).not.toHaveTextContent("Nightly report");
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Nightly report" })).toBeInTheDocument();
    await waitFor(() => expect(where).toBe("/workflows?id=nightly-report"));
    expect(screen.queryByText("Deleted Nightly report")).toBeNull();
    expect(api.writes().map((c) => `${c.method} ${c.path}`)).toEqual([
      "DELETE /api/workflows/nightly-report",
      "POST /api/workflows/nightly-report/restore",
    ]);
  });

  it("deleting a workflow a rule still uses names the conflict and keeps it", async () => {
    const user = userEvent.setup();
    fakeApi(WORKFLOW_DOCS);
    renderWorkflows("/workflows?id=review-pr");
    await screen.findByRole("heading", { level: 1, name: "Review PR" });
    await ready();
    await user.click(screen.getByRole("button", { name: "Delete workflow" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("workflows/review-pr is used by a rule");
    expect(screen.getByRole("heading", { level: 1, name: "Review PR" })).toBeInTheDocument();
  });

  it("deleting the last workflow lands on the empty state", async () => {
    const user = userEvent.setup();
    fakeApi([SPARE]);
    renderWorkflows();
    await screen.findByRole("heading", { level: 1, name: "Nightly report" });
    await ready();
    await user.click(screen.getByRole("button", { name: "Delete workflow" }));
    expect(await screen.findByRole("heading", { level: 1, name: "No workflows yet" })).toBeInTheDocument();
    expect(within(emptyState()).getByRole("button", { name: "New workflow" })).toBeInTheDocument();
  });
});
