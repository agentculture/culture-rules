import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkflowDef } from "../../api/workflows";
import { foldModel } from "../../fold/model";
import { mockFetch } from "../../test/mockApi";
import { WorkflowList } from "./WorkflowList";

/**
 * The old list pane's scenarios (src/workflows/WorkflowList.test.tsx, the row-per-workflow
 * list before the fold), on the folded list (t7): each workflow is a section of its chain
 * card, its name the link, with its (i) and enable switch, under New workflow / New rule.
 */
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
  const onNewRule = vi.fn();
  render(
    <MemoryRouter initialEntries={["/workflows"]}>
      <Routes>
        <Route
          path="/workflows"
          element={
            <>
              <WorkflowList
                model={foldModel([], workflows)}
                workflows={workflows}
                selectedId={opts.selectedId ?? null}
                slotOf={opts.slot ?? (() => null)}
                onToggle={onToggle}
                onNew={onNew}
                onNewRule={onNewRule}
                onOpen={onOpen}
              />
              <Where />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
  return { onToggle, onNew, onOpen, onNewRule, nav: screen.getByRole("navigation", { name: "Workflows" }) };
}

const section = (name: string) => screen.getByRole("group", { name: `Workflow: ${name}` });
const nameLink = (name: string) => within(section(name)).getByRole("link", { name });

afterEach(() => vi.unstubAllGlobals());

describe("the folded list pane (was WorkflowList, the Workflows tab's left pane)", () => {
  it("one section per workflow under the New workflow and New rule buttons, in a Workflows nav", () => {
    const { nav } = renderList(DOCS);
    const button = within(nav).getByRole("button", { name: "New workflow" });
    expect(button).toHaveClass("rule-list__new");
    const links = DOCS.map((d) => nameLink(d.name));
    expect(links.map((l) => l.textContent)).toEqual(["Review PR", "Build image", "Nightly report"]);
    // The New buttons come first in the pane (and in tab order).
    expect(button.compareDocumentPosition(links[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(nav).getByRole("button", { name: "New rule" })).toBeInTheDocument();
    expect(within(nav).getAllByRole("switch")).toHaveLength(3);
  });

  it("links each workflow to /workflows?id=<id>; the selected one carries aria-current", async () => {
    const user = userEvent.setup();
    const { onOpen } = renderList(DOCS, { selectedId: "build-image" });
    expect(nameLink("Build image")).toHaveAttribute("aria-current", "true");
    expect(nameLink("Review PR")).not.toHaveAttribute("aria-current");
    await user.click(nameLink("Nightly report"));
    expect(where).toBe("/workflows?id=nightly-report");
    expect(onOpen).toHaveBeenCalledWith("nightly-report");
  });

  it("Enter on a focused workflow link opens it (each link, (i) and switch is a Tab stop)", async () => {
    const user = userEvent.setup();
    renderList(DOCS);
    nameLink("Review PR").focus();
    await user.tab(); // its (i)
    expect(screen.getByRole("button", { name: "About Review PR" })).toHaveFocus();
    await user.tab(); // its switch
    expect(screen.getByRole("switch", { name: "Review PR enabled" })).toHaveFocus();
    nameLink("Build image").focus();
    await user.keyboard("{Enter}");
    expect(where).toBe("/workflows?id=build-image");
  });

  it("the workflow's (i) opens its description without opening it (d19)", async () => {
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
  });

  it("the switch shows enabled state and hands its workflow to onToggle", async () => {
    const user = userEvent.setup();
    const { onToggle } = renderList(DOCS);
    expect(screen.getByRole("switch", { name: "Review PR enabled" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("switch", { name: "Build image enabled" })).toHaveAttribute("aria-checked", "false");
    // No `enabled` field means enabled.
    expect(screen.getByRole("switch", { name: "Nightly report enabled" })).toHaveAttribute("aria-checked", "true");
    expect(section("Build image")).toHaveClass("is-disabled");
    expect(section("Review PR")).not.toHaveClass("is-disabled");
    await user.click(screen.getByRole("switch", { name: "Build image enabled" }));
    expect(onToggle).toHaveBeenCalledWith(DOCS[1]);
  });

  it("New workflow calls onNew; New rule calls onNewRule", async () => {
    const user = userEvent.setup();
    const { onNew, onNewRule } = renderList(DOCS);
    await user.click(screen.getByRole("button", { name: "New workflow" }));
    expect(onNew).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "New rule" }));
    expect(onNewRule).toHaveBeenCalledTimes(1);
  });

  it("is there with no workflows", () => {
    const { nav } = renderList([]);
    expect(within(nav).getByRole("button", { name: "New workflow" })).toBeInTheDocument();
    expect(within(nav).queryAllByRole("link")).toHaveLength(0);
    expect(nav).toHaveTextContent("No workflows yet.");
  });

  it("is there with exactly one workflow", () => {
    const { nav } = renderList([DOCS[0]], { selectedId: "review-pr" });
    expect(within(nav).getAllByRole("group", { name: /^Workflow: / })).toHaveLength(1);
    expect(nameLink("Review PR")).toHaveAttribute("aria-current", "true");
  });

  it("dots each workflow with its machine's palette slot, neutral when there is none", () => {
    renderList(DOCS, { slot: (wf) => (wf.id === "build-image" ? 1 : null) });
    expect(section("Build image").querySelector(".machine-dot")).toHaveAttribute("data-machine-slot", "1");
    expect(section("Review PR").querySelector(".machine-dot")).toHaveAttribute("data-machine-slot", "none");
  });
});
