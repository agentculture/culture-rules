import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { WorkflowDef } from "../api/workflows";
import { WorkflowList } from "./WorkflowList";
import { mockFetch } from "../test/mockApi";

const DOCS: WorkflowDef[] = [
  { id: "review-pr", name: "Review PR", enabled: true },
  { id: "build-image", name: "Build image", enabled: false },
  { id: "nightly-report", name: "Nightly report" },
];

let where = "";
function Where() {
  const loc = useLocation();
  where = `${loc.pathname}${loc.search}`;
  return null;
}

function renderList(
  workflows: WorkflowDef[],
  opts: { selectedId?: string | null; slot?: (wf: WorkflowDef) => number | null } = {},
) {
  const onToggle = vi.fn();
  const onNew = vi.fn();
  const onOpen = vi.fn();
  render(
    <MemoryRouter initialEntries={["/workflows"]}>
      <Routes>
        <Route
          path="/workflows"
          element={
            <>
              <WorkflowList
                workflows={workflows}
                selectedId={opts.selectedId ?? null}
                slotOf={opts.slot ?? (() => null)}
                onToggle={onToggle}
                onNew={onNew}
                onOpen={onOpen}
              />
              <Where />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
  return { onToggle, onNew, onOpen, nav: screen.getByRole("navigation", { name: "Workflows" }) };
}

const row = (name: string) => screen.getByRole("link", { name }).closest(".rule-row") as HTMLElement;

describe("WorkflowList (the Workflows tab's left pane)", () => {
  it("renders one row per workflow under the New workflow button, in a Workflows nav", () => {
    const { nav } = renderList(DOCS);
    const button = within(nav).getByRole("button", { name: "New workflow" });
    expect(button).toHaveClass("rule-list__new");
    const links = within(nav).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual(["Review PR", "Build image", "Nightly report"]);
    // The New button comes first in the pane (and in tab order).
    expect(button.compareDocumentPosition(links[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(nav).getAllByRole("switch")).toHaveLength(3);
  });

  it("links each row to /workflows?id=<id>; the selected row carries aria-current", async () => {
    const user = userEvent.setup();
    const { onOpen } = renderList(DOCS, { selectedId: "build-image" });
    expect(screen.getByRole("link", { name: "Build image" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("link", { name: "Review PR" })).not.toHaveAttribute("aria-current");
    expect(row("Build image")).toHaveClass("is-selected");
    expect(row("Review PR")).not.toHaveClass("is-selected");
    await user.click(screen.getByRole("link", { name: "Nightly report" }));
    expect(where).toBe("/workflows?id=nightly-report");
    expect(onOpen).toHaveBeenCalledWith("nightly-report");
  });

  it("Enter on a focused row opens it (the rows are Tab stops)", async () => {
    const user = userEvent.setup();
    renderList(DOCS);
    await user.tab(); // New workflow
    await user.tab(); // Review PR
    expect(screen.getByRole("link", { name: "Review PR" })).toHaveFocus();
    await user.tab(); // its (i)
    expect(screen.getByRole("button", { name: "About Review PR" })).toHaveFocus();
    await user.tab(); // its switch
    expect(screen.getByRole("switch", { name: "Review PR enabled" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("link", { name: "Build image" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(where).toBe("/workflows?id=build-image");
  });

  it("the row's (i) opens its description without opening the row (d19)", async () => {
    const user = userEvent.setup();
    const { fetchMock } = mockFetch({
      "/api/workflows/build-image/describe": {
        body: { id: "build-image", kind: "workflow", lines: ["1 build — code on spark"], entries: [] },
      },
    });
    const { onOpen } = renderList(DOCS);
    const before = where;
    await user.click(screen.getByRole("button", { name: "About Build image" }));
    const panel = await screen.findByRole("dialog", { name: "About Build image" });
    expect(await within(panel).findByTestId("about-lines")).toHaveTextContent("1 build — code on spark");
    expect(where).toBe(before);
    expect(onOpen).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });

  it("the row switch shows enabled state and hands its workflow to onToggle", async () => {
    const user = userEvent.setup();
    const { onToggle } = renderList(DOCS);
    expect(screen.getByRole("switch", { name: "Review PR enabled" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("switch", { name: "Build image enabled" })).toHaveAttribute("aria-checked", "false");
    // No `enabled` field means enabled, as on Rules.
    expect(screen.getByRole("switch", { name: "Nightly report enabled" })).toHaveAttribute("aria-checked", "true");
    expect(row("Build image")).toHaveClass("is-disabled");
    expect(row("Review PR")).not.toHaveClass("is-disabled");
    await user.click(screen.getByRole("switch", { name: "Build image enabled" }));
    expect(onToggle).toHaveBeenCalledWith(DOCS[1]);
  });

  it("New workflow calls onNew", async () => {
    const user = userEvent.setup();
    const { onNew } = renderList(DOCS);
    await user.click(screen.getByRole("button", { name: "New workflow" }));
    expect(onNew).toHaveBeenCalledTimes(1);
  });

  it("is there with no workflows and with one", () => {
    const { nav } = renderList([]);
    expect(within(nav).getByRole("button", { name: "New workflow" })).toBeInTheDocument();
    expect(within(nav).queryAllByRole("link")).toHaveLength(0);
  });

  it("is there with exactly one workflow", () => {
    const { nav } = renderList([DOCS[0]], { selectedId: "review-pr" });
    expect(within(nav).getAllByRole("link")).toHaveLength(1);
    expect(within(nav).getByRole("link", { name: "Review PR" })).toHaveAttribute("aria-current", "true");
  });

  it("dots each row with its machine's palette slot, neutral when there is none", () => {
    renderList(DOCS, { slot: (wf) => (wf.id === "build-image" ? 1 : null) });
    expect(row("Build image").querySelector(".machine-dot")).toHaveAttribute("data-machine-slot", "1");
    expect(row("Review PR").querySelector(".machine-dot")).toHaveAttribute("data-machine-slot", "none");
  });
});
