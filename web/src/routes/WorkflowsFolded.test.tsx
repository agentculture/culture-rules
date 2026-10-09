import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Workflows from "./Workflows";
import { getAgentState, resetAgentState } from "../agent-state/store";
import * as client from "../api/client";
import { defaultRoutes } from "../test/mockApi";
import { MACHINES, WHOAMI } from "../fixtures/rules-fixture";
import { mockFetch, type Routes as ApiRoutes } from "../test/mockApi";
import { LIVE_DEBOUNCE_MS, setLiveSourceFactory } from "../api/live";
import { FakeEventSource } from "../test/fakeEventSource";
import { createFakeApi, fetchFor } from "../rules/fake-api";
import { DOMMatrixStub, MeasuringResizeObserver } from "../test/reactFlow";
import { VIEW_MODE_KEY } from "../workflows/views/mode";
import {
  ACTORS,
  REPOS,
  REVIEW_PR,
  REVIEW_PR_ID,
  REVIEW_RULE_ID,
  WORKFLOW_DOCS,
  WORKFLOW_RULES,
  workflowRunsFor,
} from "../workflows/fixture";

/**
 * The folded Workflows tab (t8): the list (t7), the view switch (t5) with the
 * Simple view (t6) as its default, the D7 place for a rule with no workflow,
 * and "New rule" — the Rules tab's jobs, now here.
 */
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
    "/api/repos": { body: { items: REPOS } },
    "/api/asks": { body: { items: [] } },
    ...overrides,
  };
}

let where = "";
function Where() {
  const loc = useLocation();
  where = `${loc.pathname}${loc.search}`;
  return null;
}

function renderAt(path: string) {
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

describe("Workflows, folded (t8)", () => {
  beforeEach(() => {
    localStorage.removeItem(VIEW_MODE_KEY);
    resetAgentState();
    vi.stubGlobal("ResizeObserver", MeasuringResizeObserver);
    vi.stubGlobal("DOMMatrixReadOnly", DOMMatrixStub);
    mockFetch(routes());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("a workflow opens in the Simple view, at the entry point asked for, with the switch to Detailed and Debug", async () => {
    renderAt(`/workflows?id=${REVIEW_PR_ID}&entry=${REVIEW_RULE_ID}`);
    const group = await screen.findByRole("group", { name: "Canvas view" });
    expect(within(group).getByRole("button", { name: "Simple" })).toHaveAttribute("aria-pressed", "true");
    // The Simple view (not its placeholder) is mounted, with this workflow's entry point.
    expect(screen.queryByText("The When / Then view is on its way.")).toBeNull();
    expect((await screen.findAllByText("Review on approve")).length).toBeGreaterThan(0);
    await waitFor(() =>
      expect(getAgentState().workflows).toMatchObject({
        selected: REVIEW_PR_ID,
        entry: REVIEW_RULE_ID,
        view: "simple",
      }),
    );
    expect(getAgentState().workflows?.entries).toContain(REVIEW_RULE_ID);
    await userEvent.click(within(group).getByRole("button", { name: "Debug" }));
    await waitFor(() => expect(getAgentState().workflows?.view).toBe("debug"));
  });

  it("the D7 place: a rule with no workflow shows the offer to give it one", async () => {
    renderAt("/workflows?entry=clean-caches");
    const place = await screen.findByRole("region", { name: "Rule without a workflow: Clean caches" });
    expect(within(place).getByRole("button", { name: /create its workflow/i })).toBeInTheDocument();
    // The list names it under rules without a workflow too, linking back here.
    const list = screen.getByRole("navigation", { name: "Workflows" });
    expect(within(list).getByRole("link", { name: "Clean caches" })).toHaveAttribute(
      "href",
      "/workflows?entry=clean-caches",
    );
    await waitFor(() => expect(getAgentState().workflows?.without_workflow).toContain("clean-caches"));
  });

  it("New rule opens the rule form in place of the Rules tab's", async () => {
    renderAt(`/workflows?id=${REVIEW_PR_ID}`);
    const list = await screen.findByRole("navigation", { name: "Workflows" });
    await userEvent.click(within(list).getByRole("button", { name: "New rule" }));
    expect(await screen.findByText(/When does this happen\?/)).toBeInTheDocument();
    expect(where).toBe(`/workflows?id=${REVIEW_PR_ID}`);
  });

  it("while New rule or the D7 place is shown, the head names it and offers no workflow's delete or run", async () => {
    renderAt(`/workflows?id=${REVIEW_PR_ID}`);
    const list = await screen.findByRole("navigation", { name: "Workflows" });
    await userEvent.click(within(list).getByRole("button", { name: "New rule" }));
    expect(await screen.findByRole("heading", { level: 1, name: "New rule" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete workflow" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    expect(within(list).queryAllByRole("link", { current: true })).toHaveLength(0);
    // Cancel gives focus back to New rule.
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(within(list).getByRole("button", { name: "New rule" })).toHaveFocus());
  });

  it("the D7 place's head is the rule, not some other workflow", async () => {
    renderAt("/workflows?entry=clean-caches");
    expect(await screen.findByRole("heading", { level: 1, name: "Clean caches" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete workflow" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
  });

  it("a redirect that could not read the rules says so, never that the rule was deleted", async () => {
    renderAt("/workflows?notice=rules-unavailable&rule=some-rule");
    expect(await screen.findByText(/Could not look up rule “some-rule” just now/)).toBeInTheDocument();
    expect(screen.queryByText(/may have been deleted/)).toBeNull();
  });

  it("a workflow id that is not there is named, not silently swapped for another", async () => {
    renderAt("/workflows?id=gone-flow&entry=x");
    expect(await screen.findByText(/No workflow “gone-flow”/)).toBeInTheDocument();
    // Nothing unrelated opens in its place: no other workflow's head, delete or run.
    expect(screen.queryByRole("button", { name: "Delete workflow" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    await waitFor(() => expect(getAgentState().workflows?.selected).toBeNull());
  });

  it("a rule pointing at a missing workflow stays editable there: its entry point, not another workflow", async () => {
    mockFetch(
      routes({
        "/api/rules": {
          body: {
            items: [
              ...WORKFLOW_RULES,
              { ...WORKFLOW_RULES[0], id: "stranded", name: "Stranded rule", workflow: { id: "gone-flow" } },
            ],
          },
        },
      }),
    );
    renderAt("/workflows?id=gone-flow&entry=stranded");
    expect(await screen.findByRole("heading", { level: 1, name: "Missing workflow gone-flow" })).toBeInTheDocument();
    expect(screen.getByText(/No workflow “gone-flow”/)).toBeInTheDocument();
    const place = screen.getByRole("region", { name: "Rules of the missing workflow gone-flow" });
    expect((await within(place).findAllByText("Stranded rule")).length).toBeGreaterThan(0);
    // Its entry point keeps its own controls (enable / disable at least).
    expect(within(place).getAllByRole("switch").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Delete workflow" })).toBeNull();
  });
});

/** Items handed over from the t8 review (t9): one live stream, agent-state's view, no flash. */
describe("Workflows, folded: t8 review follow-ups (t9)", () => {
  let api: ApiRoutes;
  beforeEach(() => {
    localStorage.removeItem(VIEW_MODE_KEY);
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
    vi.restoreAllMocks();
  });

  it("the Simple view and the list share one live stream, and a rules change reaches both", async () => {
    renderAt(`/workflows?id=${REVIEW_PR_ID}`);
    await screen.findByRole("group", { name: "Entry point: Review on approve" });
    const open = FakeEventSource.instances.filter((s) => !s.closed);
    expect(open).toHaveLength(1);
    const url = new URL(open[0].url, "http://x");
    expect(url.searchParams.get("collections")?.split(",").sort()).toEqual([
      "asks", "rule_decisions", "rules", "runs", "workflows",
    ]);

    // A new entry point for review-pr arrives: the list and the Simple view both show it.
    api["/api/rules"] = {
      body: {
        items: [
          ...WORKFLOW_RULES,
          {
            id: "late-reviewer",
            name: "Late reviewer",
            trigger: { kind: "event", params: { type: "github.review.submitted" } },
            workflow: { id: REVIEW_PR_ID, inputs: {} },
            action: { kind: "noop" },
            enabled: true,
          },
        ],
      },
    };
    act(() => open[0].change("rules", "late-reviewer"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, LIVE_DEBOUNCE_MS + 30));
    });
    expect(await screen.findByRole("group", { name: "Entry point: Late reviewer" })).toBeInTheDocument();
    const list = screen.getByRole("navigation", { name: "Workflows" });
    await waitFor(() => expect(within(list).getAllByRole("link", { name: "Late reviewer" }).length).toBeGreaterThan(0));
    expect(FakeEventSource.instances.filter((s) => !s.closed)).toHaveLength(1);
  });

  it("agent-state reports no view where no view switch is mounted (the D7 place, New rule), and the view again after", async () => {
    renderAt(`/workflows?id=${REVIEW_PR_ID}`);
    const group = await screen.findByRole("group", { name: "Canvas view" });
    await userEvent.click(within(group).getByRole("button", { name: "Debug" }));
    await waitFor(() => expect(getAgentState().workflows?.view).toBe("debug"));
    const list = screen.getByRole("navigation", { name: "Workflows" });
    await userEvent.click(within(list).getByRole("button", { name: "New rule" }));
    await screen.findByRole("heading", { level: 1, name: "New rule" });
    await waitFor(() => expect(getAgentState().workflows?.view).toBeNull());
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(getAgentState().workflows?.view).toBe("debug"));

    await userEvent.click(within(list).getByRole("link", { name: "Clean caches" }));
    await screen.findByRole("heading", { level: 1, name: "Clean caches" });
    await waitFor(() => expect(getAgentState().workflows?.view).toBeNull());
  });

  it("agent-state reports no view on an empty list", async () => {
    mockFetch({ ...routes(), "/api/workflows": { body: { items: [] } }, "/api/rules": { body: { items: [] } } });
    renderAt("/workflows");
    await screen.findByRole("region", { name: "No workflows yet" });
    await waitFor(() => expect(getAgentState().status).toBe("ready"));
    expect(getAgentState().workflows?.view).toBeNull();
  });

  it("creating from the D7 place never flashes 'No workflow' while the list reloads", async () => {
    const fake = createFakeApi(NOW);
    const serve = fetchFor(fake);
    let created = false;
    let release: () => void = () => {};
    const held = new Promise<void>((r) => {
      release = r;
    });
    vi.stubGlobal("fetch", (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (method === "POST" && url.split("?")[0].endsWith("/workflows")) created = true;
      // The list's reload after the create is held, so the page sits between the two loads.
      if (created && method === "GET" && url.split("?")[0].endsWith("/api/workflows")) await held;
      return serve(input, init);
    }) as typeof fetch);
    renderAt("/workflows?entry=clean-caches");
    const place = await screen.findByRole("region", { name: "Rule without a workflow: Clean caches" });
    await userEvent.click(within(place).getByRole("button", { name: "Create its workflow" }));
    await waitFor(() => expect(new URLSearchParams(where.split("?")[1]).get("id")).toBe("clean-caches"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(screen.queryByText(/No workflow “clean-caches”/)).toBeNull();
    // While it opens, agent-state is not ready: nothing is selected yet (t9 review).
    expect(screen.getByText("Opening clean-caches…")).toBeInTheDocument();
    expect(getAgentState()).toMatchObject({ status: "loading", view_ready: false });
    // Never some other workflow in its place either.
    expect(screen.queryByRole("heading", { level: 1, name: "Build image" })).toBeNull();
    await act(async () => release());
    expect(await screen.findByRole("heading", { level: 1, name: "Clean caches" })).toBeInTheDocument();
    expect(screen.queryByText(/No workflow “clean-caches”/)).toBeNull();
    await waitFor(() => expect(getAgentState().status).toBe("ready"));
    expect(getAgentState().workflows?.selected).toBe("clean-caches");
  });
});

/**
 * The Rules board's own scenarios (src/routes/Rules.test.tsx before the fold) that are about
 * the page, not one rule: where every rule is listed, agent-state, and a failed load. With the
 * rules fixture (src/fixtures/rules-fixture.ts), Build and publish starts build-image and the
 * other four have no workflow yet.
 */
describe("Workflows, folded: the Rules board's page scenarios (t9)", () => {
  beforeEach(() => {
    localStorage.removeItem(VIEW_MODE_KEY);
    resetAgentState();
    vi.stubGlobal("ResizeObserver", MeasuringResizeObserver);
    vi.stubGlobal("DOMMatrixReadOnly", DOMMatrixStub);
    mockFetch(defaultRoutes(NOW));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("every rule is listed, as an entry point or under rules without a workflow, the first affordance New workflow / New rule", async () => {
    renderAt("/workflows");
    const list = await screen.findByRole("navigation", { name: "Workflows" });
    const buttons = within(list).getAllByRole("button");
    expect(buttons.slice(0, 2).map((b) => b.textContent)).toEqual(["New workflow", "New rule"]);
    await waitFor(() => expect(within(list).getByRole("link", { name: "Build and publish" })).toHaveAttribute(
      "href",
      "/workflows?id=build-image&entry=build-and-publish",
    ));
    const without = within(list).getByRole("region", { name: "Rules without a workflow" });
    expect(within(without).getAllByRole("link").map((a) => a.textContent)).toEqual([
      "Review on approve", "Clean caches", "Train batch", "Triage bugs",
    ]);
    // One stored workflow's switch per stored workflow; a rule's own switch is on its entry point.
    expect(within(list).getAllByRole("switch")).toHaveLength(2);
  });

  it("reports ready in agent-state: the entry point open, and the deprecated rules alias with its stages", async () => {
    renderAt("/workflows?id=build-image&entry=build-and-publish");
    await waitFor(() => expect(getAgentState().status).toBe("ready"));
    await waitFor(() => expect(getAgentState().workflows).toMatchObject({
      selected: "build-image",
      entry: "build-and-publish",
      entries: ["build-and-publish"],
      without_workflow: ["review-on-approve", "clean-caches", "train-batch", "triage-bugs"],
    }));
    expect(getAgentState().rules).toMatchObject({
      count: 5,
      selected: "build-and-publish",
      stages: ["trigger", "condition", "workflow", "action"],
    });
    expect(getAgentState().errors).toEqual([]);
  });

  it("names a failure that has no string form and still reports ready", async () => {
    // A rejection reason with no string form: String(reason) throws while the load is applied.
    vi.spyOn(client, "listMachines").mockRejectedValue(Object.create(null));
    renderAt("/workflows?id=build-image");
    await waitFor(() => expect(screen.getAllByRole("alert").map((a) => a.textContent).join("|")).toContain("unexpected error"));
    await waitFor(() => expect(getAgentState().status).toBe("ready"));
    // Only the machines failed: the rest of the load still applies (the workflow is open).
    expect(screen.getByRole("heading", { level: 1, name: "Build image" })).toBeInTheDocument();
    expect(getAgentState().errors).toEqual(["unexpected error"]);
  });

  it("names a load failure and still reports ready", async () => {
    mockFetch({
      ...defaultRoutes(NOW),
      "/api/rules": { status: 503, body: { error: { code: "store_down", message: "store unreachable", errors: [] } } },
    });
    renderAt("/workflows?id=build-image");
    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent("store unreachable");
    await waitFor(() => expect(getAgentState().status).toBe("ready"));
    expect(getAgentState().errors).toEqual(["store unreachable"]);
  });
});
