import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { Rule, Condition } from "../../api/types";
import type { WorkflowDef } from "../../api/workflows";
import { foldModel } from "../../fold/model";
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
const compare = (id: string): Condition => ({ op: "compare", cmp: "==", left: { field: "data.workflow_id" }, right: { literal: id } });

describe("same-event notes (Chain view)", () => {
  it("derives same-event notes across chains from the PR fixer fixture", () => {
    const renamed = workflows.map(wf => wf.id === "report-secrets" ? { ...wf, name: "Secret reporter" } : wf);
    render(<MemoryRouter><ChainView model={foldModel(rules, renamed)} /></MemoryRouter>);
    const entry = screen.getByRole("link", { name: rules.find(rule => rule.id === "pr-fixer-checks")!.name }).closest("fieldset")!;
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
    render(<MemoryRouter><ChainView model={foldModel(entries, wf)} /></MemoryRouter>);
    const source = screen.getByRole("link", { name: "source" }).closest("fieldset") as HTMLElement;
    expect(within(source).getAllByText("same event starts Beta")).toHaveLength(1);
    for (const name of ["different-kind", "different-type", "no-type", "also-no-type"]) {
      const row = screen.getByRole("link", { name }).closest("fieldset") as HTMLElement;
      expect(within(row).queryByText(/same event starts/)).not.toBeInTheDocument();
    }
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
    // #35: the queue's workflows join the fixer's chain (queue-add, queue-progress); d34: queue-stop;
    // #39: a try that hit capacity_exhausted rejoins through queue-add-capacity
    expect(screen.getByText("24 rules → 11 entry points, 13 continuations, 8 workflows")).toBeInTheDocument();
    expect(screen.getAllByRole("group", { name: /^Entry point:/ })).toHaveLength(11);
    expect(screen.getAllByRole("group", { name: /^Workflow:/ })).toHaveLength(8);
    const edges = [...container.querySelectorAll("path[data-continuation-id]")];
    expect(edges.map((edge) => [edge.getAttribute("data-source"), edge.getAttribute("data-target")])).toEqual([
      ["review-commit", "publish-fix"],
      ["pr-fix", "queue-progress"], ["queue-add-capacity", "queue-progress"], ["pr-fix", "queue-progress"],
      ["pr-fix", "queue-progress"], ["queue-stop", "queue-progress"], ["pr-fix", "queue-progress"],
      ["queue-add", "queue-progress"], ["review-commit", "queue-add"], ["pr-fix", "queue-add-capacity"],
      ["pr-fix", "queue-add"], ["pr-fix", "queue-add"],
      ["pr-fix", "review-commit"],
    ]);
    expect(container.querySelectorAll("path[data-entry-id]")).toHaveLength(11);
    for (const rule of rules) expect(screen.getByText(`was ${rule.id}`)).toBeInTheDocument();
  });

  it("draws an either continuation from each of its predecessors (d3)", () => {
    const wf = [{ id: "a", name: "Alpha" }, { id: "b", name: "Beta" }, { id: "c", name: "Gamma" }];
    const either: Rule = { id: "after-either", name: "After either", workflow: { id: "c" },
      trigger: { kind: "event", params: { type: "rules.run.succeeded" } },
      condition: { op: "or", args: [compare("a"), compare("b")] }, action: { kind: "noop" } };
    const model = foldModel([either], wf);
    const { container } = render(<MemoryRouter><ChainView model={model} chain={model.chains[0]} /></MemoryRouter>);
    const edges = [...container.querySelectorAll("path[data-continuation-id='after-either']")];
    expect(edges.map((edge) => [edge.getAttribute("data-source"), edge.getAttribute("data-target")])).toEqual([["a", "c"], ["b", "c"]]);
    expect(screen.queryByRole("region", { name: "Continuations without a visible predecessor" })).not.toBeInTheDocument();
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
