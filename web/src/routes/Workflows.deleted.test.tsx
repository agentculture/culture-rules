import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Workflows from "./Workflows";
import { resetAgentState } from "../agent-state/store";
import { resetWhoamiForTests } from "../hooks/useWhoami";
import { MACHINES, WHOAMI } from "../fixtures/rules-fixture";
import { mockFetch, type Routes as ApiRoutes } from "../test/mockApi";
import { DOMMatrixStub, MeasuringResizeObserver, useMeasuredLayout } from "../test/reactFlow";
import { ACTORS, REVIEW_PR, WORKFLOW_DOCS, WORKFLOW_RULES, workflowRunsFor } from "../workflows/fixture";

const NOW = Date.parse("2026-10-03T12:00:00Z");
const GONE = {
  ...REVIEW_PR,
  id: "old-flow",
  name: "Old flow",
  deleted_at: "2026-10-02T10:00:00Z",
  deleted_by: "ori",
  restorable_until: "2026-10-09T10:00:00Z",
};

function routes(overrides: ApiRoutes = {}, whoami: unknown = WHOAMI): ApiRoutes {
  return {
    "/api/whoami": { body: whoami },
    "/api/rules": { body: { items: WORKFLOW_RULES } },
    "/api/machines": { body: { items: MACHINES } },
    "/api/workflows": { body: { items: [...WORKFLOW_DOCS, GONE] } },
    "/api/workflows/review-pr": { body: REVIEW_PR },
    "/api/actors": { body: { items: ACTORS } },
    "/api/runs": { body: { items: workflowRunsFor(NOW) } },
    "/api/repos": { body: { items: [] } },
    "/api/workflows/old-flow/purge": { body: { collection: "workflows", id: "old-flow", applied: false } },
    "/api/workflows/old-flow/restore": { body: { ...GONE, deleted_at: null } },
    ...overrides,
  };
}

function renderBoard() {
  return render(
    <MemoryRouter initialEntries={["/workflows?id=review-pr"]}>
      <Routes>
        <Route path="/workflows" element={<Workflows />} />
      </Routes>
    </MemoryRouter>,
  );
}

useMeasuredLayout();

type Call = [string, RequestInit | undefined];
let fetchMock: ReturnType<typeof mockFetch>["fetchMock"];
const posts = (path: string) =>
  (fetchMock.mock.calls as unknown as Call[]).filter(
    ([url, init]) => init?.method === "POST" && url.split("?")[0] === path,
  );
const purgeBodies = () => posts("/api/workflows/old-flow/purge").map(([, init]) => JSON.parse(String(init?.body)));

function start(overrides: ApiRoutes = {}, whoami: unknown = WHOAMI) {
  fetchMock = mockFetch(routes(overrides, whoami)).fetchMock;
  renderBoard();
}
const showDeleted = async () => {
  await screen.findByRole("heading", { level: 1, name: "Review PR" });
  await userEvent.click(screen.getByRole("button", { name: "Show deleted" }));
  return screen.findByText("Old flow");
};

describe("Workflows: soft-deleted view and admin purge", () => {
  beforeEach(() => {
    resetAgentState();
    resetWhoamiForTests();
    vi.stubGlobal("ResizeObserver", MeasuringResizeObserver);
    vi.stubGlobal("DOMMatrixReadOnly", DOMMatrixStub);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("hides deleted workflows by default and toggles include_deleted", async () => {
    start();
    await screen.findByRole("heading", { level: 1, name: "Review PR" });
    expect(screen.queryByText("Old flow")).not.toBeInTheDocument();
    expect((fetchMock.mock.calls as unknown as Call[]).some(([u]) => u.includes("include_deleted"))).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Show deleted" }));
    expect(await screen.findByText("Old flow")).toBeInTheDocument();
    expect((fetchMock.mock.calls as unknown as Call[]).some(([u]) => u.includes("include_deleted=true"))).toBe(true);
    const row = document.querySelector('[data-workflow-id="old-flow"]') as HTMLElement;
    expect(row).toHaveClass("is-deleted");
    expect(within(row).getByText("deleted")).toBeInTheDocument();
    expect(row).toHaveTextContent(/restorable until/i);
    await userEvent.click(screen.getByRole("button", { name: "Show deleted" }));
    await waitFor(() => expect(screen.queryByText("Old flow")).not.toBeInTheDocument());
  });

  it("never shows Purge to a non-admin, even with deleted workflows shown", async () => {
    start({}, { identity: "eve", kind: "sso", roles: ["viewer", "editor"] });
    await showDeleted();
    expect(screen.getByRole("button", { name: /restore old flow/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /purge/i })).not.toBeInTheDocument();
  });

  it("shows Purge to an admin on deleted rows only", async () => {
    start();
    await showDeleted();
    expect(screen.getAllByRole("button", { name: /purge/i })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Purge Old flow" })).toBeInTheDocument();
  });

  it("restores a deleted workflow from the list", async () => {
    start();
    await showDeleted();
    await userEvent.click(screen.getByRole("button", { name: /restore old flow/i }));
    await waitFor(() => expect(posts("/api/workflows/old-flow/restore")).toHaveLength(1));
    expect(await screen.findByRole("switch", { name: "Old flow enabled" })).toBeInTheDocument();
  });

  it("purges only after a dry run and an explicit confirmation", async () => {
    start();
    await showDeleted();
    await userEvent.click(screen.getByRole("button", { name: "Purge Old flow" }));
    await screen.findByText(/permanently/i);
    expect(purgeBodies()).toEqual([{ apply: false }]);
    await userEvent.click(screen.getByRole("button", { name: /confirm purge/i }));
    await waitFor(() => expect(purgeBodies()).toEqual([{ apply: false }, { apply: true }]));
    await waitFor(() => expect(screen.queryByText("Old flow")).not.toBeInTheDocument());
  });

  it("cancelling the confirmation never applies the purge", async () => {
    start();
    await showDeleted();
    await userEvent.click(screen.getByRole("button", { name: "Purge Old flow" }));
    await userEvent.click(await screen.findByRole("button", { name: /cancel/i }));
    expect(purgeBodies().every((b) => b.apply === false)).toBe(true);
  });

  it("explains a rule_referenced refusal in guided words", async () => {
    start({
      "/api/workflows/old-flow/purge": {
        status: 409,
        body: { error: { code: "rule_referenced", message: "RAW SERVER TEXT", errors: [] } },
      },
    });
    await showDeleted();
    await userEvent.click(screen.getByRole("button", { name: "Purge Old flow" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/still depend/i);
    expect(screen.queryByText(/RAW SERVER TEXT/)).not.toBeInTheDocument();
    expect(purgeBodies().some((b) => b.apply === true)).toBe(false);
  });
});
