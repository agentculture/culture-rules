import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { render, screen, within, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { Rule, Condition } from "../../api/types";
import type { WorkflowDef } from "../../api/workflows";
import { foldModel } from "../../fold/model";
import { WorkflowList } from "./WorkflowList";
import { ChainView } from "./ChainView";

function fixture<T>(directory: string): T[] {
  const path = resolve(dirname(fileURLToPath(import.meta.url)), "../../../../docs/rules/pr-fixer", directory);
  return readdirSync(path).filter((name) => name.endsWith(".json")).sort()
    .map((name) => JSON.parse(readFileSync(resolve(path, name), "utf8")) as T);
}
const workflows = fixture<WorkflowDef>("workflows");
const rules = fixture<Rule>("rules");
const model = foldModel(rules, workflows);
const props = { workflows, model, selectedId: null, slotOf: () => null, onToggle: vi.fn(), onNew: vi.fn() };
const compare = (id: string): Condition => ({ op: "compare", cmp: "==", left: { field: "data.workflow_id" }, right: { literal: id } });

describe("folded workflow list", () => {
  it("renders the checked-in PR fixer as two cards with measured d2 counts and every workflow summary", () => {
    render(<MemoryRouter><WorkflowList {...props} /></MemoryRouter>);
    const cards = screen.getAllByRole("article", { name: /^Chain:/ });
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByText("3 workflows · 4 entry points · was 7 rules")).toBeInTheDocument();
    expect(within(cards[1]).getByText("1 workflow · 2 entry points · was 2 rules")).toBeInTheDocument();
    expect(within(cards[0]).getAllByRole("group", { name: /^Workflow:/ })).toHaveLength(3);
    expect(within(cards[1]).getAllByRole("group", { name: /^Workflow:/ })).toHaveLength(1);
    for (const label of ["Starts when", "Continues into", "Runs", "Ends with"]) {
      expect(screen.getAllByText(label)).toHaveLength(4);
    }
    for (const rule of rules) expect(screen.getAllByText(rule.name).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Disabled").length).toBeGreaterThanOrEqual(9);
    expect(screen.getAllByText(/pr-fixer:\{trigger.data.repository\}/).length).toBeGreaterThan(0);
  });

  it("keeps selection, open, toggle, pending and new callbacks compatible", () => {
    const onOpen = vi.fn(), onToggle = vi.fn(), onNew = vi.fn();
    render(<MemoryRouter><WorkflowList {...props} selectedId={workflows[0].id} onOpen={onOpen}
      onToggle={onToggle} onNew={onNew} pending={new Set([workflows[1].id])} /></MemoryRouter>);
    const link = within(screen.getByRole("group", { name: `Workflow: ${workflows[0].name}` })).getByRole("link", { name: workflows[0].name });
    expect(link).toHaveAttribute("aria-current", "true");
    fireEvent.click(link);
    expect(onOpen).toHaveBeenCalledWith(workflows[0].id);
    fireEvent.click(screen.getByRole("switch", { name: `${workflows[0].name} enabled` }));
    expect(onToggle).toHaveBeenCalledWith(workflows[0]);
    const pendingSwitch = screen.getByRole("switch", { name: `${workflows[1].name} enabled` });
    expect(pendingSwitch).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(pendingSwitch);
    expect(onToggle).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "New workflow" }));
    expect(onNew).toHaveBeenCalledOnce();
  });

  it("opens the chosen chain and returns to the list", () => {
    render(<MemoryRouter><WorkflowList {...props} /></MemoryRouter>);
    fireEvent.click(screen.getAllByRole("button", { name: "See it as one chain" })[0]);
    expect(screen.getAllByRole("group", { name: /^Entry point:/ })).toHaveLength(4);
    expect(screen.getByText("7 rules → 4 entry points, 3 continuations, 3 workflows")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "List" }));
    expect(screen.getAllByRole("article", { name: /^Chain:/ })).toHaveLength(2);
  });
});

describe("Chain view", () => {
  it("draws all fixture starts, workflows and directed continuations, including the return edge", () => {
    const { container } = render(<MemoryRouter><ChainView model={model} /></MemoryRouter>);
    expect(screen.getByText("9 rules → 6 entry points, 3 continuations, 4 workflows")).toBeInTheDocument();
    expect(screen.getAllByRole("group", { name: /^Entry point:/ })).toHaveLength(6);
    expect(screen.getAllByRole("group", { name: /^Workflow:/ })).toHaveLength(4);
    const edges = [...container.querySelectorAll("path[data-continuation-id]")];
    expect(edges.map((edge) => [edge.getAttribute("data-source"), edge.getAttribute("data-target")])).toEqual([
      ["review-commit", "publish-fix"], ["review-commit", "pr-fix"], ["pr-fix", "review-commit"],
    ]);
    expect(container.querySelectorAll("path[data-entry-id]")).toHaveLength(6);
    for (const rule of rules) expect(screen.getByText(`was ${rule.id}`)).toBeInTheDocument();
  });

  it("uses dynamic counts and names, preserving ambiguous, unscoped and empty predecessors", () => {
    const wf = [{ id: "custom", name: "Custom work" }];
    const entries: Rule[] = [undefined, compare(""), { op: "and", args: [compare("a"), compare("b")] } as Condition]
      .map((condition, index) => ({ id: `r${index}`, name: `Start ${index}`, workflow: { id: "custom" },
        trigger: { kind: "event", params: { type: "rules.run.failed" } }, condition, action: { kind: "noop" } }));
    const { container } = render(<MemoryRouter><ChainView model={foldModel(entries, wf)} /></MemoryRouter>);
    expect(screen.getByText("3 rules → 0 entry points, 3 continuations, 1 workflow")).toBeInTheDocument();
    expect(screen.getByText(/from any workflow/)).toBeInTheDocument();
    expect(screen.getByText(/from an empty workflow id/)).toBeInTheDocument();
    expect(screen.getByText(/from multiple workflow terms/)).toBeInTheDocument();
    expect(container.querySelectorAll("path[data-continuation-id]")).toHaveLength(0);
  });

  it("handles an empty model without fabricated counts", () => {
    render(<MemoryRouter><ChainView model={foldModel([], [])} /></MemoryRouter>);
    expect(screen.getByText("0 rules → 0 entry points, 0 continuations, 0 workflows")).toBeInTheDocument();
    expect(screen.queryAllByRole("group", { name: /^Workflow:/ })).toHaveLength(0);
  });
});
