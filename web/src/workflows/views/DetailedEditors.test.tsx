import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetAgentState } from "../../agent-state/store";
import type { Rule } from "../../api/types";
import { MACHINES } from "../../fixtures/rules-fixture";
import { createFakeApi, fetchFor, handle } from "../../rules/fake-api";
import { DOMMatrixStub, MeasuringResizeObserver, useMeasuredLayout } from "../../test/reactFlow";
import { ACTORS, REVIEW_PR } from "../fixture";
import { FOLD_RULES, FOLD_WORKFLOWS } from "../simple/fixture";
import { VIEW_MODE_KEY } from "./mode";
import WorkflowViews from "./WorkflowViews";

useMeasuredLayout();

const NOW = Date.parse("2026-10-09T12:00:00Z");
const PR_FIX = { ...REVIEW_PR, id: "publish-fix", name: "Publish the fix", notes: "d21: quiet period" };

function renderDetailed(workflow = PR_FIX, twoEntries = true) {
  const fake = createFakeApi(NOW);
  fake.rules = structuredClone(FOLD_RULES) as Rule[];
  // two entry points with one condition, so a shared condition is edited once for both
  if (twoEntries) {
  const publish = fake.rules.find((r) => r.id === "pr-fixer-publish")!;
  fake.rules.splice(fake.rules.findIndex((r) => r.id === "any-failure"), 1, { ...structuredClone(publish), id: "publish-2", name: "Publish again" });
  }
  fake.workflows = structuredClone(FOLD_WORKFLOWS);
  handle(fake, "GET", "/rules", new URLSearchParams());
  vi.stubGlobal("fetch", fetchFor(fake));
  window.localStorage.setItem(VIEW_MODE_KEY, "detailed");
  const noop = vi.fn();
  return render(
    <MemoryRouter>
      <WorkflowViews
        workflow={workflow}
        machines={MACHINES}
        actors={ACTORS}
        overlay={null}
        selected={null}
        onSelect={noop}
        onToggle={noop}
        onPlacement={noop}
        onEdit={noop}
        onDelete={noop}
        onAddStep={noop}
        onOpenIo={noop}
        onConnect={noop}
        onRefused={noop}
      />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  resetAgentState();
  vi.stubGlobal("ResizeObserver", MeasuringResizeObserver);
  vi.stubGlobal("DOMMatrixReadOnly", DOMMatrixStub);
  window.localStorage.clear();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/**
 * Every field Simple edited before t14 (t15 criterion 1), with where Detailed edits it.
 * Simple edited these through simple/EntryCard, SharedForms and ThenColumn; Detailed mounts
 * those same components. The last four go beyond the plan's list: Simple edited them too.
 */
describe("Detailed edits every field Simple edited", () => {
  it("finds an editor for each", async () => {
    renderDetailed();
    const region = await screen.findByRole("region", { name: "Entry points and Then" });
    const name = "Publish when approved";
    const entry = await within(region).findByRole("group", { name: `Entry point: ${name}` });
    const open = entry; // the first entry point opens first, as in Simple

    // entry trigger (and the rest of the rule's own form)
    expect(within(open).getByRole("button", { name: `Edit ${name}` })).toBeInTheDocument();
    // attempt counting
    expect(within(open).getByRole("switch", { name: "Counts toward the attempt budget" })).toBeInTheDocument();
    // placement (shared by every entry point)
    const shared = within(region).getByRole("group", { name: "Shared by every entry point" });
    expect(within(shared).getByRole("button", { name: /evaluates/ })).toBeInTheDocument();
    // shared conditions
    expect(within(shared).getByRole("button", { name: "Add a condition for every entry point" })).toBeInTheDocument();
    // continues from (a continuation's predecessor)
    expect(within(open).getByLabelText("Continues from")).toBeInTheDocument();
    // continues into: Simple only showed read-only links; Detailed shows the same links
    expect(within(region).getByRole("group", { name: "Continues into" })).toBeInTheDocument();
    // ends here, on failure, runs
    expect(within(region).getByRole("button", { name: "Edit ends here" })).toBeInTheDocument();
    expect(within(region).getByRole("button", { name: "Edit on failure" })).toBeInTheDocument();
    expect(within(region).getByRole("button", { name: "Edit runs" })).toBeInTheDocument();
    // beyond the plan's list: the entry's own on failure, runs, group and priority, order
    expect(within(open).getByRole("button", { name: `On failure for ${name}` })).toBeInTheDocument();
    expect(within(open).getByRole("button", { name: `Runs for ${name}` })).toBeInTheDocument();
    expect(within(open).getByRole("button", { name: `Group and priority for ${name}` })).toBeInTheDocument();
    expect(within(open).getByRole("button", { name: /Run order/ })).toBeInTheDocument();
    // new entry point, enable switch is on the card, delete
    expect(within(region).getByRole("button", { name: "Entry point" })).toBeInTheDocument();
    expect(within(open).getByRole("button", { name: `Delete ${name}` })).toBeInTheDocument();
  });
});

describe("a condition on one entry point", () => {
  it("is added in the entry's own form when the entries' conditions differ", async () => {
    renderDetailed({ ...PR_FIX, id: "pr-fix" }, false);
    const region = await screen.findByRole("region", { name: "Entry points and Then" });
    const entry = await within(region).findByRole("group", { name: "Entry point: Checks settled, not green" });
    expect(within(entry).getByRole("button", { name: "Add condition" })).toBeInTheDocument();
  });
});

describe("notes in Detailed (t15 criterion 2)", () => {
  it("edits the workflow's notes in the inputs panel", async () => {
    const { default: IoEditor } = await import("../IoEditor");
    const onChange = vi.fn();
    render(<IoEditor side="in" workflow={PR_FIX} onChange={onChange} onClose={vi.fn()} />);
    const notes = screen.getByLabelText("Workflow notes");
    expect(notes).toHaveValue("d21: quiet period");
    await userEvent.type(notes, "!");
    expect(onChange).toHaveBeenLastCalledWith({ ...PR_FIX, notes: "d21: quiet period!" });
  });
});
