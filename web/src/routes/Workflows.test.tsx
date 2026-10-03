import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import Workflows from "./Workflows";
import { getAgentState, resetAgentState } from "../agent-state/store";
import { workflowsState } from "../workflows/agentState";
import { MACHINES, WHOAMI } from "../fixtures/rules-fixture";
import { mockFetch, type Routes as ApiRoutes } from "../test/mockApi";
import { act } from "@testing-library/react";
import { LIVE_DEBOUNCE_MS, setLiveSourceFactory } from "../api/live";
import { FakeEventSource } from "../test/fakeEventSource";
import {
  ACTORS,
  EXPORT_RESULT,
  IMPORT_FILE_NAME,
  IMPORT_FILE_TEXT,
  REPOS,
  REVIEW_PR,
  RUN_7,
  STARTED_RUN,
  WORKFLOW_DOCS,
  WORKFLOW_RULES,
  importPlan,
  workflowRunsFor,
} from "../workflows/fixture";

const NOW = Date.parse("2026-10-03T12:00:00Z");

function routes(overrides: ApiRoutes = {}): ApiRoutes {
  return {
    "/api/whoami": { body: WHOAMI },
    "/api/rules": { body: { items: WORKFLOW_RULES } },
    "/api/machines": { body: { items: MACHINES } },
    "/api/workflows": { body: { items: WORKFLOW_DOCS } },
    "/api/workflows/review-pr": { body: REVIEW_PR },
    "/api/actors": { body: { items: ACTORS } },
    "/api/runs": { body: { items: workflowRunsFor(NOW) } },
    "/api/runs/run-7": { body: RUN_7 },
    "/api/export": { body: EXPORT_RESULT },
    "/api/import": { body: importPlan(false) },
    "/api/repos": { body: { items: REPOS } },
    ...overrides,
  };
}

let where = "";
function Where() {
  const loc = useLocation();
  where = `${loc.pathname}${loc.search}`;
  return null;
}

function renderWorkflows(path = "/workflows?id=review-pr") {
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

type Call = [string, RequestInit | undefined];
const callsOf = (fetchMock: ReturnType<typeof mockFetch>["fetchMock"]) =>
  fetchMock.mock.calls as unknown as Call[];
const methodCalls = (fetchMock: ReturnType<typeof mockFetch>["fetchMock"], method: string, path: string) =>
  callsOf(fetchMock).filter(([url, init]) => (init?.method ?? "GET") === method && url.split("?")[0] === path);

/**
 * jsdom lays nothing out, and React Flow keeps an unmeasured node
 * `visibility: hidden` (which also blanks its accessible name). Give every
 * element a size and a ResizeObserver that reports it, as React Flow's own
 * testing guide does, so the canvas renders as it would in a browser.
 */
class MeasuringResizeObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}
  observe(target: Element) {
    const size = { width: 190, height: 120 };
    const box = [{ inlineSize: 190, blockSize: 120 }];
    const entry = { target, contentRect: size, borderBoxSize: box, contentBoxSize: box };
    this.callback([entry as unknown as ResizeObserverEntry], this as unknown as ResizeObserver);
  }
  unobserve() {}
  disconnect() {}
}
class DOMMatrixStub {
  m22 = 1;
}
const sized = ["offsetWidth", "offsetHeight"] as const;
const original = sized.map((k) => Object.getOwnPropertyDescriptor(HTMLElement.prototype, k));
beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, get: () => 190 });
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get: () => 120 });
});
afterAll(() => {
  sized.forEach((k, i) => {
    if (original[i]) Object.defineProperty(HTMLElement.prototype, k, original[i]!);
  });
});

/** A step (or the Inputs / Outputs) card on the canvas, by its accessible name. */
const card = (name: string) => screen.getByRole("group", { name });

async function loaded() {
  await screen.findByRole("heading", { level: 1, name: "Review PR" });
  await waitFor(() => expect(getAgentState().status).toBe("ready"));
}

describe("Workflows board (Chosen — Workflows)", () => {
  let fetchMock: ReturnType<typeof mockFetch>["fetchMock"];

  beforeEach(() => {
    resetAgentState();
    vi.stubGlobal("ResizeObserver", MeasuringResizeObserver);
    vi.stubGlobal("DOMMatrixReadOnly", DOMMatrixStub);
    fetchMock = mockFetch(routes()).fetchMock;
    URL.createObjectURL = vi.fn(() => "blob:export");
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("heads the board with the workflow name, version and the io controls", async () => {
    renderWorkflows();
    await loaded();
    expect(screen.getByText("v3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /agentculture\/workflows/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run" })).toBeInTheDocument();
    expect(screen.getByText("same machine")).toBeInTheDocument();
    expect(screen.getByText("crosses machines")).toBeInTheDocument();
  });

  it("draws inputs, every step with its machine and enable switch, and outputs", async () => {
    renderWorkflows();
    await loaded();
    await waitFor(() => expect(card("Review")).toBeInTheDocument());
    for (const name of ["Inputs", "Fetch diff", "Run tests", "Decide", "Outputs"]) {
      expect(card(name)).toBeInTheDocument();
    }
    const review = card("Review");
    expect(review).toHaveTextContent("thor");
    expect(review).toHaveTextContent("claude, opus");
    expect(review).toHaveAttribute("data-machine-slot", "1");
    expect(within(review).getByRole("switch", { name: "Review enabled" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    // Typed ports: every port carries its type.
    const diff = review.querySelector('[data-port="in:diff"]');
    expect(diff).toHaveAttribute("data-port-type", "string");
    expect(review.querySelector('[data-port="out:findings"]')).toHaveAttribute("data-port-type", "array");
  });

  it("toggling a step marks the workflow dirty and Save PUTs only schema fields", async () => {
    const user = userEvent.setup();
    renderWorkflows();
    await loaded();
    await waitFor(() => expect(card("Review")).toBeInTheDocument());
    await user.click(within(card("Review")).getByRole("switch", { name: "Review enabled" }));
    expect(within(card("Review")).getByRole("switch", { name: "Review enabled" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(workflowsState()?.dirty).toBe(true);
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(methodCalls(fetchMock, "PUT", "/api/workflows/review-pr")).toHaveLength(1));
    const body = JSON.parse(methodCalls(fetchMock, "PUT", "/api/workflows/review-pr")[0][1]!.body as string);
    expect(body.steps.find((s: { id: string }) => s.id === "review").enabled).toBe(false);
    expect(body).not.toHaveProperty("deleted_at");
  });

  it("selecting a step shows its toolbar; placement switches machine | actor | requirement", async () => {
    const user = userEvent.setup();
    renderWorkflows();
    await loaded();
    await waitFor(() => expect(card("Review")).toBeInTheDocument());
    fireEvent.click(within(card("Review")).getByText("Review"));
    const chip = await screen.findByRole("button", { name: "on thor" });
    expect(screen.getByRole("button", { name: "Edit Review" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete Review" })).toBeInTheDocument();

    await user.click(chip);
    const dialog = await screen.findByRole("dialog", { name: "Placement of Review" });
    await user.click(within(dialog).getByRole("radio", { name: "Via an actor" }));
    await user.selectOptions(within(dialog).getByRole("combobox", { name: "Actor" }), "claude-reviewer");
    await user.click(within(dialog).getByRole("button", { name: "Apply" }));
    expect(await screen.findByRole("button", { name: "via claude-reviewer" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "via claude-reviewer" }));
    const again = await screen.findByRole("dialog", { name: "Placement of Review" });
    await user.click(within(again).getByRole("radio", { name: "Needs capabilities" }));
    const req = within(again).getByRole("textbox", { name: "Capabilities" });
    await user.clear(req);
    await user.type(req, "gpu");
    await user.click(within(again).getByRole("button", { name: "Apply" }));
    expect(await screen.findByRole("button", { name: "needs gpu" })).toBeInTheDocument();
    expect(card("Review")).toHaveAttribute("data-machine-slot", "none");
  });

  it("deletes a step from its toolbar and adds one with +", async () => {
    const user = userEvent.setup();
    renderWorkflows();
    await loaded();
    await waitFor(() => expect(card("Run tests")).toBeInTheDocument());
    fireEvent.click(within(card("Run tests")).getByText("Run tests"));
    await user.click(await screen.findByRole("button", { name: "Delete Run tests" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Run tests" })).toBeNull());
    await user.click(screen.getByRole("button", { name: "Add step" }));
    await waitFor(() => expect(workflowsState()?.steps).toContain("step-1"));
  });

  it("the step editor edits typed ports and wires inputs to compatible sources only", async () => {
    const user = userEvent.setup();
    renderWorkflows();
    await loaded();
    await waitFor(() => expect(card("Decide")).toBeInTheDocument());
    fireEvent.click(within(card("Decide")).getByText("Decide"));
    await user.click(await screen.findByRole("button", { name: "Edit Decide" }));
    const editor = await screen.findByRole("dialog", { name: "Edit Decide" });
    const wire = within(editor).getByRole("combobox", { name: "passed comes from" });
    const options = within(wire).getAllByRole("option").map((o) => o.textContent);
    expect(options).toContain("Run tests · passed");
    expect(options).not.toContain("Fetch diff · diff");
    await user.selectOptions(within(editor).getByRole("combobox", { name: "Type of input passed" }), "string");
    await user.click(within(editor).getByRole("button", { name: "Done" }));
    expect(card("Decide").querySelector('[data-port="in:passed"]')).toHaveAttribute("data-port-type", "string");
  });

  it("Export calls GET /export and offers the bundle as a download", async () => {
    const user = userEvent.setup();
    renderWorkflows();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Export" }));
    await waitFor(() => expect(methodCalls(fetchMock, "GET", "/api/export")).toHaveLength(1));
    expect(methodCalls(fetchMock, "GET", "/api/export")[0][0]).toBe("/api/export?format=json");
    expect(await screen.findByRole("status")).toHaveTextContent("Exported 2 files");
  });

  it("Import posts the chosen files as a dry run, then applies the plan", async () => {
    const user = userEvent.setup();
    renderWorkflows();
    await loaded();
    const file = new File([IMPORT_FILE_TEXT], IMPORT_FILE_NAME, { type: "application/json" });
    await user.upload(screen.getByLabelText("Import files"), file);
    await waitFor(() => expect(methodCalls(fetchMock, "POST", "/api/import")).toHaveLength(1));
    const dry = JSON.parse(methodCalls(fetchMock, "POST", "/api/import")[0][1]!.body as string);
    expect(dry).toEqual({ files: { "workflows/triage.json": IMPORT_FILE_TEXT }, apply: false });
    const plan = await screen.findByRole("dialog", { name: "Import plan" });
    expect(plan).toHaveTextContent("create");
    expect(plan).toHaveTextContent("workflows/triage.json");
    await user.click(within(plan).getByRole("button", { name: "Apply import" }));
    await waitFor(() => expect(methodCalls(fetchMock, "POST", "/api/import")).toHaveLength(2));
    const applied = JSON.parse(methodCalls(fetchMock, "POST", "/api/import")[1][1]!.body as string);
    expect(applied.apply).toBe(true);
  });

  it("the repo picker lists repositories from GET /repos", async () => {
    const user = userEvent.setup();
    renderWorkflows();
    await loaded();
    const picker = await screen.findByRole("button", { name: /agentculture\/workflows/ });
    expect(methodCalls(fetchMock, "GET", "/api/repos")).toHaveLength(1);
    await user.click(picker);
    const menu = await screen.findByRole("listbox", { name: "Repository" });
    await user.click(within(menu).getByRole("option", { name: "agentculture/rules-lab" }));
    expect(screen.getByRole("button", { name: /agentculture\/rules-lab/ })).toBeInTheDocument();
  });

  it("the repo picker exports to and imports from the chosen repository: dry-run, then apply", async () => {
    const plan = (applied: boolean) => ({
      repo: "agentculture/workflows",
      applied,
      committed: applied,
      pushed: false,
      commit: applied ? "abc123" : null,
      changes: [{ kind: "rules", id: "r1", path: "rules/r1.json", action: "add" }],
    });
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input).split("?")[0];
      const body = init?.body ? JSON.parse(init.body as string) : null;
      const json = (data: unknown) =>
        new Response(JSON.stringify(data), { status: 200, headers: { "content-type": "application/json" } });
      if (url === "/api/export" && init?.method === "POST") return json(plan(Boolean(body.apply)));
      if (url === "/api/import" && init?.method === "POST") return json(importPlan(Boolean(body.apply)));
      const route = routes()[url];
      return route ? json(route.body) : new Response("{}", { status: 404 });
    });
    const user = userEvent.setup();
    renderWorkflows();
    await loaded();
    const picker = await screen.findByRole("button", { name: /agentculture\/workflows/ });

    await user.click(picker);
    await user.click(screen.getByRole("button", { name: "Export to repo" }));
    const exportPlan = await screen.findByRole("dialog", { name: "Export plan" });
    expect(exportPlan).toHaveTextContent("rules/r1.json");
    await user.click(within(exportPlan).getByRole("button", { name: "Apply export" }));
    await waitFor(() => expect(methodCalls(fetchMock, "POST", "/api/export")).toHaveLength(2));
    const exports = methodCalls(fetchMock, "POST", "/api/export").map(([, i]) => JSON.parse(i!.body as string));
    expect(exports).toEqual([
      { repo: "agentculture/workflows", apply: false },
      { repo: "agentculture/workflows", apply: true },
    ]);
    await screen.findByText(/Exported 1 change to agentculture\/workflows/);

    await user.click(picker);
    await user.click(screen.getByRole("button", { name: "Import from repo" }));
    const importDialog = await screen.findByRole("dialog", { name: "Import plan" });
    await user.click(within(importDialog).getByRole("button", { name: "Apply import" }));
    await waitFor(() => expect(methodCalls(fetchMock, "POST", "/api/import")).toHaveLength(2));
    const imports = methodCalls(fetchMock, "POST", "/api/import").map(([, i]) => JSON.parse(i!.body as string));
    expect(imports).toEqual([
      { repo: "agentculture/workflows", apply: false },
      { repo: "agentculture/workflows", apply: true },
    ]);
  });

  it("a run lights each step with its host and outcome from GET /runs/{id}", async () => {
    renderWorkflows("/workflows?id=review-pr&run=run-7");
    await loaded();
    await waitFor(() => expect(methodCalls(fetchMock, "GET", "/api/runs/run-7")).toHaveLength(1));
    await waitFor(() => expect(card("Decide")).toHaveAttribute("data-run-status", "failed"));
    expect(card("Decide")).toHaveTextContent(/failed on spark/);
    expect(card("Review")).toHaveAttribute("data-run-status", "succeeded");
    expect(card("Review")).toHaveTextContent(/succeeded on thor/);
    expect(card("Run tests")).toHaveTextContent(/succeeded on spark2/);
    await waitFor(() => expect(workflowsState()?.run).toEqual({ id: "run-7", status: "failed" }));
  });

  it("Run starts a run through the rule that uses this workflow and overlays it", async () => {
    const base = routes();
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const path = url.split("?")[0];
      const body =
        (path === "/api/runs" && init?.method === "POST") || path === "/api/runs/run-8"
          ? STARTED_RUN
          : (base[path]?.body ?? {});
      return new Response(JSON.stringify(body), {
        status: path === "/api/runs" && init?.method === "POST" ? 201 : 200,
        headers: { "content-type": "application/json" },
      });
    });
    const user = userEvent.setup();
    renderWorkflows();
    await loaded();
    await user.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(where).toContain("run=run-8"));
    const post = methodCalls(fetchMock, "POST", "/api/runs");
    expect(JSON.parse(post[0][1]!.body as string)).toEqual({ rule_id: "review-on-approve" });
    await waitFor(() => expect(card("Fetch diff")).toHaveAttribute("data-run-status", "running"));
  });

  it("switching workflow uses ?id= and agent-state reports the tab", async () => {
    const user = userEvent.setup();
    renderWorkflows();
    await loaded();
    expect(getAgentState().tab).toBe("workflows");
    expect(workflowsState()).toMatchObject({
      count: 2,
      selected: "review-pr",
      steps: ["fetch-diff", "run-tests", "review", "decide"],
      dirty: false,
      run: null,
    });
    await user.selectOptions(screen.getByRole("combobox", { name: "Workflow" }), "build-image");
    expect(await screen.findByRole("heading", { level: 1, name: "Build image" })).toBeInTheDocument();
    expect(where).toBe("/workflows?id=build-image");
  });

  it("a failed load is an alert, and the tab still settles ready", async () => {
    mockFetch(routes({ "/api/actors": { status: 500, body: { error: { code: "boom", message: "actors down", errors: [] } } } }));
    renderWorkflows();
    await loaded();
    expect(screen.getByRole("alert")).toHaveTextContent("actors down");
  });
});

describe("Workflows tab live updates (h61 / c80)", () => {
  let api: ApiRoutes;

  beforeEach(() => {
    resetAgentState();
    FakeEventSource.reset();
    setLiveSourceFactory(FakeEventSource.factory);
    vi.stubGlobal("ResizeObserver", MeasuringResizeObserver);
    vi.stubGlobal("DOMMatrixReadOnly", DOMMatrixStub);
    api = routes();
    mockFetch(api);
  });
  afterEach(() => {
    setLiveSourceFactory(undefined);
    vi.unstubAllGlobals();
  });

  async function emit(collection: string, id: string) {
    act(() => FakeEventSource.latest().change(collection, id));
    await act(async () => {
      await new Promise((r) => setTimeout(r, LIVE_DEBOUNCE_MS + 30));
    });
  }

  it("subscribes to workflows and runs", async () => {
    renderWorkflows();
    await loaded();
    const url = new URL(FakeEventSource.latest().url, "http://x");
    expect(url.searchParams.get("collections")?.split(",").sort()).toEqual(["runs", "workflows"]);
  });

  it("a runs change re-reads the overlaid run, so the overlay follows it", async () => {
    renderWorkflows("/workflows?id=review-pr&run=run-7");
    await loaded();
    await waitFor(() => expect(card("Decide")).toHaveAttribute("data-run-status", "failed"));
    api["/api/runs/run-7"] = {
      body: {
        ...RUN_7,
        status: "succeeded",
        steps: RUN_7.steps.map((st) =>
          st.key === "decide" ? { ...st, status: "succeeded", host: "thor", error: null } : st,
        ),
      },
    };
    await emit("runs", "run-7");
    await waitFor(() => expect(card("Decide")).toHaveAttribute("data-run-status", "succeeded"));
    expect(card("Decide")).toHaveTextContent(/succeeded on thor/);
  });

  it("a workflows change elsewhere refetches the list without a reload", async () => {
    renderWorkflows();
    await loaded();
    api["/api/workflows"] = {
      body: {
        items: WORKFLOW_DOCS.map((w) => (w.id === "review-pr" ? { ...w, name: "Review PR, renamed" } : w)),
      },
    };
    await emit("workflows", "review-pr");
    expect(await screen.findByRole("heading", { level: 1, name: "Review PR, renamed" })).toBeInTheDocument();
  });
});
