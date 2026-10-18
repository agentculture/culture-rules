import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkflowDef } from "../../api/workflows";
import { PANE_KEY, readPaneCollapsed, writePaneCollapsed } from "./pane";
import { WorkflowPane } from "./WorkflowPane";

const wf = (id: string, name: string, extra: Partial<WorkflowDef> = {}): WorkflowDef =>
  ({ id, name, version: 1, steps: [{ id: "s1", kind: "logic" }], description: "Long prose that must not show", ...extra }) as WorkflowDef;
const DOCS = [wf("review-pr", "Review PR"), wf("build-image", "Build image"), wf("nightly", "Nightly report", { enabled: false })];

function Harness({ initial = false }: Readonly<{ initial?: boolean }>) {
  const [collapsed, setCollapsed] = useState(initial);
  return (
    <MemoryRouter>
      <WorkflowPane
        workflows={DOCS}
        selectedId="build-image"
        collapsed={collapsed}
        onToggle={() => setCollapsed((c) => !c)}
        onNew={vi.fn()}
        onNewRule={vi.fn()}
      />
    </MemoryRouter>
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("WorkflowPane: compact list and search (t21 criterion 1)", () => {
  it("lists names only, one short row each, the open one marked current", () => {
    render(<Harness />);
    const list = screen.getByRole("navigation", { name: "Workflows" });
    const rows = within(list).getAllByRole("link");
    expect(rows.map((a) => a.textContent)).toEqual(["Review PR", "Build image", "Nightly report"]);
    expect(rows.filter((a) => a.getAttribute("aria-current") === "page").map((a) => a.textContent)).toEqual(["Build image"]);
    expect(rows[0]).toHaveAttribute("href", "/workflows?id=review-pr");
    // names only: no chain cards, counts, steps, descriptions, switches or About buttons
    expect(list).not.toHaveTextContent(/step|entry point|Long prose|chain/i);
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByRole("article")).toBeNull();
  });

  it("filters names case-insensitively as you type, and Escape clears the search", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const search = screen.getByRole("searchbox", { name: "Search workflows" });
    await user.type(search, "REVIEW");
    expect(screen.getAllByRole("link").map((a) => a.textContent)).toEqual(["Review PR"]);
    await user.clear(search);
    await user.type(search, "zzz");
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(screen.getByText("No match")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(search).toHaveValue("");
    expect(screen.getAllByRole("link")).toHaveLength(3);
  });

  it("keeps New workflow and New rule as small buttons in the pane", () => {
    render(<Harness />);
    const pane = screen.getByRole("complementary");
    expect(within(pane).getByRole("button", { name: "New workflow" })).toBeInTheDocument();
    expect(within(pane).getByRole("button", { name: "New rule" })).toBeInTheDocument();
  });
});

describe("WorkflowPane: collapse to a rail (t21 criterion 2)", () => {
  it("collapses to only the toggle and expands back, by keyboard, with aria-expanded", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const toggle = screen.getByRole("button", { name: "Hide workflows" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    toggle.focus();
    await user.keyboard("{Enter}");
    const rail = screen.getByRole("complementary");
    expect(rail).toHaveClass("wf-pane--rail");
    expect(within(rail).getAllByRole("button")).toHaveLength(1);
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    const shown = screen.getByRole("button", { name: "Show workflows" });
    expect(shown).toHaveAttribute("aria-expanded", "false");
    expect(shown).toHaveFocus();
    await user.keyboard(" ");
    expect(screen.getByRole("button", { name: "Hide workflows" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByRole("link")).toHaveLength(3);
  });

  it("starts collapsed when told to", () => {
    render(<Harness initial />);
    expect(screen.getByRole("button", { name: "Show workflows" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Workflows" })).toBeNull();
  });
});

describe("pane choice in localStorage (t21 criterion 2)", () => {
  it("is remembered per viewer", () => {
    expect(readPaneCollapsed()).toBe(false);
    writePaneCollapsed(true);
    expect(localStorage.getItem(PANE_KEY)).toBe("collapsed");
    expect(readPaneCollapsed()).toBe(true);
    writePaneCollapsed(false);
    expect(readPaneCollapsed()).toBe(false);
  });

  it("works without storage: reads expanded, writes quietly", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readPaneCollapsed()).toBe(false);
    expect(() => writePaneCollapsed(true)).not.toThrow();
    fireEvent.click(render(<Harness />).getByRole("button", { name: "Hide workflows" }));
    expect(screen.getByRole("button", { name: "Show workflows" })).toBeInTheDocument();
  });
});
