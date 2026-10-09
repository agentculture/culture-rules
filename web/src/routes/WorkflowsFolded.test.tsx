import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Workflows from "./Workflows";
import { getAgentState, resetAgentState } from "../agent-state/store";
import { MACHINES, WHOAMI } from "../fixtures/rules-fixture";
import { mockFetch, type Routes as ApiRoutes } from "../test/mockApi";
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
});
