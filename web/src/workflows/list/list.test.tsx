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
import { triggerText } from "./presentation";
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
    expect(within(cards[0]).getByText("6 workflows linked by continuations · 9 entry points · was 19 rules")).toBeInTheDocument();
    expect(within(cards[1]).getByText("1 workflow linked by continuations · 2 entry points · was 2 rules")).toBeInTheDocument();
    expect(within(cards[0]).getAllByRole("group", { name: /^Workflow:/ })).toHaveLength(6);
    expect(within(cards[1]).getAllByRole("group", { name: /^Workflow:/ })).toHaveLength(1);
    for (const label of ["Starts when", "Continues into", "Runs", "Ends with"]) {
      expect(screen.getAllByText(label)).toHaveLength(7);
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

  it("dims disabled workflows and keeps terminal wording and one continuation destination link", () => {
    const disabled = workflows.map((wf, index) => ({ ...wf, enabled: index !== 0 }));
    render(<MemoryRouter><WorkflowList {...props} workflows={disabled} model={foldModel(rules, disabled)} /></MemoryRouter>);
    expect(screen.getByRole("group", { name: `Workflow: ${disabled[0].name}` })).toHaveClass("is-disabled");
    expect(screen.getAllByText("Nothing. The chain ends here.").length).toBeGreaterThan(0);
    for (const entry of model.continuations) {
      const source = screen.getByRole("group", { name: `Workflow: ${workflows.find(wf => wf.id === entry.fromWorkflowId)!.name}` });
      const dd = within(source).getByText("Continues into").nextElementSibling!;
      expect(dd.querySelectorAll(`a[href="/workflows?id=${entry.workflowId}&entry=${entry.rule.id}"]`)).toHaveLength(1);
    }
  });

  it("lists D7 candidates with names, rule links and an inclusive total", () => {
    const candidates = ["loose / one", "loose-two"].map(id => ({ ...rules[0], id, name: id, workflow: undefined }));
    const mixed = foldModel([...rules, ...candidates], workflows);
    render(<MemoryRouter><WorkflowList {...props} model={mixed} /></MemoryRouter>);
    const section = screen.getByRole("region", { name: "Rules without a workflow" });
    for (const rule of candidates) expect(within(section).getByRole("link", { name: rule.name })).toHaveAttribute("href", `/workflows?entry=${encodeURIComponent(rule.id)}`);
    expect(within(section).getAllByText("D7 candidate · can get a workflow of its own, with no steps yet")).toHaveLength(2);
    expect(screen.getByText("7 workflows · 11 entry points · was 23 rules")).toBeInTheDocument();
  });

  it("derives same-event notes across chains from the PR fixer fixture", () => {
    const renamed = workflows.map(wf => wf.id === "report-secrets" ? { ...wf, name: "Secret reporter" } : wf);
    render(<MemoryRouter><WorkflowList {...props} workflows={renamed} model={foldModel(rules, renamed)} /></MemoryRouter>);
    const entry = screen.getByRole("link", { name: rules.find(rule => rule.id === "pr-fixer-checks")!.name }).closest(".fold-entry")!;
    expect(within(entry as HTMLElement).getByText("same event starts Secret reporter")).toBeInTheDocument();
    expect(within(entry as HTMLElement).queryByText(/same event starts.*pr-fix/)).not.toBeInTheDocument();
  });

  it("matches D1 on both trigger kind and type, deduplicating destination workflows", () => {
    const wf = [{ id: "a", name: "Alpha" }, { id: "b", name: "Beta" }];
    const entry = (id: string, workflow: string, kind: string, type?: string): Rule => ({
      id, name: id, workflow: { id: workflow }, trigger: { kind, params: { type } }, action: { kind: "noop" },
    });
    const entries = [
      entry("source", "a", "event", "custom.event"),
      entry("same-workflow", "a", "event", "custom.event"),
      entry("match", "b", "event", "custom.event"),
      entry("duplicate", "b", "event", "custom.event"),
      entry("different-kind", "b", "probe", "custom.event"),
      entry("different-type", "b", "event", "another.event"),
      entry("no-type", "a", "manual"),
      entry("also-no-type", "b", "manual"),
    ];
    render(<MemoryRouter><WorkflowList {...props} workflows={wf} model={foldModel(entries, wf)} /></MemoryRouter>);
    const source = screen.getByRole("link", { name: "source" }).closest(".fold-entry") as HTMLElement;
    expect(within(source).getAllByText("same event starts Beta")).toHaveLength(1);
    for (const name of ["different-kind", "different-type", "no-type", "also-no-type"]) {
      const row = screen.getByRole("link", { name }).closest(".fold-entry") as HTMLElement;
      expect(within(row).queryByText(/same event starts/)).not.toBeInTheDocument();
    }
  });

  it("returns focus to the second card when that chain was opened", () => {
    render(<MemoryRouter><WorkflowList {...props} /></MemoryRouter>);
    fireEvent.click(screen.getAllByRole("button", { name: "See it as one chain" })[1]);
    expect(screen.getByRole("heading", { level: 2 })).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "List" }));
    expect(screen.getAllByRole("button", { name: "See it as one chain" })[1]).toHaveFocus();
  });

  it("opens the chosen chain and returns to the list", () => {
    render(<MemoryRouter><WorkflowList {...props} /></MemoryRouter>);
    fireEvent.click(screen.getAllByRole("button", { name: "See it as one chain" })[0]);
    expect(screen.getByRole("heading", { level: 2 })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Chain" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "List" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getAllByRole("group", { name: /^Entry point:/ })).toHaveLength(9);
    expect(screen.getByText("19 rules → 9 entry points, 10 continuations, 6 workflows")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "List" }));
    expect(screen.getAllByRole("article", { name: /^Chain:/ })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "See it as one chain" })[0]).toHaveFocus();
  });
});

describe("Chain view", () => {
  it("keeps the diagram's scroll region a keyboard tab stop", () => {
    render(<MemoryRouter><ChainView model={model} /></MemoryRouter>);
    expect(screen.getByRole("region", { name: "Workflow chain diagram" })).toHaveAttribute("tabindex", "0");
  });

  it("reads trigger params as they always did; only a plain object reads as JSON", () => {
    const rule = (params: Record<string, unknown>) => ({ ...rules[0], trigger: { kind: "probe", params } }) as Rule;
    expect(triggerText(rule({ command: ["a", "b"] }))).toBe("Probe: a,b");
    expect(triggerText(rule({ command: 3 }))).toBe("Probe: 3");
    expect(triggerText(rule({ command: { argv: ["x"] } }))).toBe('Probe: {"argv":["x"]}');
    expect(triggerText(rule({}))).toBe("Probe: not set");
  });

  it("draws all fixture starts, workflows and directed continuations, including the return edge", () => {
    const { container } = render(<MemoryRouter><ChainView model={model} /></MemoryRouter>);
    // #35: the queue's workflows join the fixer's chain (queue-add, queue-progress); d34: queue-stop
    expect(screen.getByText("21 rules → 11 entry points, 10 continuations, 7 workflows")).toBeInTheDocument();
    expect(screen.getAllByRole("group", { name: /^Entry point:/ })).toHaveLength(11);
    expect(screen.getAllByRole("group", { name: /^Workflow:/ })).toHaveLength(7);
    const edges = [...container.querySelectorAll("path[data-continuation-id]")];
    expect(edges.map((edge) => [edge.getAttribute("data-source"), edge.getAttribute("data-target")])).toEqual([
      ["review-commit", "publish-fix"],
      ["pr-fix", "queue-progress"], ["pr-fix", "queue-progress"], ["pr-fix", "queue-progress"],
      ["queue-stop", "queue-progress"], ["pr-fix", "queue-progress"], ["queue-add", "queue-progress"],
      ["review-commit", "queue-add"], ["pr-fix", "queue-add"], ["pr-fix", "review-commit"],
    ]);
    expect(container.querySelectorAll("path[data-entry-id]")).toHaveLength(11);
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
    expect(container.querySelector(".fold-chain__canvas")).toHaveStyle({ height: "360px" });
  });

  it("handles an empty model without fabricated counts", () => {
    render(<MemoryRouter><ChainView model={foldModel([], [])} /></MemoryRouter>);
    expect(screen.getByText("0 rules → 0 entry points, 0 continuations, 0 workflows")).toBeInTheDocument();
    expect(screen.queryAllByRole("group", { name: /^Workflow:/ })).toHaveLength(0);
  });
});
