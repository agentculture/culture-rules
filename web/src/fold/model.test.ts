import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { Condition, Rule, Workflow } from "../api/types";
import { foldModel, sharedValues } from "./model";

function fixture<T>(directory: string): T[] {
  const path = resolve(dirname(fileURLToPath(import.meta.url)), "../../../docs/rules/pr-fixer", directory);
  return readdirSync(path).filter((name) => name.endsWith(".json")).sort()
    .map((name) => JSON.parse(readFileSync(resolve(path, name), "utf8")) as T);
}
const workflows: Workflow[] = ["a", "b", "c"].map((id) => ({ id, name: id }));
const compare = (id = "a"): Condition => ({
  op: "compare", cmp: "==", left: { field: "data.workflow_id" }, right: { literal: id },
});
const rule = (id: string, changes: Partial<Rule> = {}): Rule => ({
  id, name: id, trigger: { kind: "manual" }, workflow: { id: "b" },
  action: { kind: "noop" }, ...changes,
});
const run = (condition?: Condition | null): Partial<Rule> => ({
  trigger: { kind: "event", params: { type: "rules.run.succeeded" } }, condition,
});

describe("foldModel", () => {
  it("derives the approved PR fixer counts from checked-in JSON (d2)", () => {
    const rules = fixture<Rule>("rules");
    const model = foldModel(rules, fixture<Workflow>("workflows"));
    expect(model.workflows).toHaveLength(4);
    expect(model.entryPoints).toHaveLength(6);
    expect(model.continuations).toHaveLength(3);
    expect(model.chains.map((chain) => chain.workflowIds.slice().sort())).toEqual([
      ["pr-fix", "publish-fix", "review-commit"], ["report-secrets"],
    ]);
    expect(model.workflows.find((wf) => wf.id === "pr-fix")?.entries.map((entry) => entry.rule.id))
      .toContain("pr-fixer-refix");
    expect(model.workflows.find((wf) => wf.id === "report-secrets")?.entries).toHaveLength(2);
    expect(model.workflows.flatMap((wf) => wf.entries)).toHaveLength(9);
    expect(model.continuations.map((entry) => [entry.rule.id, entry.fromWorkflowId, entry.workflowId]))
      .toEqual([
        ["pr-fixer-publish", "review-commit", "publish-fix"],
        ["pr-fixer-refix", "review-commit", "pr-fix"],
        ["pr-fixer-review-commit", "pr-fix", "review-commit"],
      ]);
    expect(model.workflows.flatMap((wf) => wf.entries).every((entry) => !entry.enabled)).toBe(true);
    expect(model.d7Candidates).toEqual([]);
  });

  it.each([
    compare(),
    { op: "and", args: [{ op: "and", args: [compare()] }] } as Condition,
    { op: "compare", cmp: "==", left: { literal: "a" }, right: { field: "data.workflow_id" } } as Condition,
  ])("links an unconditional equality term: %j", (condition) => {
    const model = foldModel([rule("r", run(condition))], workflows);
    expect(model.continuations[0].fromWorkflowId).toBe("a");
    expect(model.chains.map((chain) => chain.workflowIds)).toEqual([["a", "b"], ["c"]]);
  });

  it.each([
    null, undefined,
    { op: "or", args: [compare()] } as Condition,
    { op: "not", arg: compare() } as Condition,
    { op: "and", args: [{ op: "or", args: [compare()] }] } as Condition,
    { ...compare(), cmp: "!=" } as Condition,
    { op: "compare", cmp: "==", left: { field: "workflow.id" }, right: { literal: "a" } } as Condition,
    { op: "compare", cmp: "==", left: { field: "data.workflow_id" }, right: { var: "a" } } as Condition,
  ])("labels run events without a qualifying term as from any workflow: %j", (condition) => {
    const model = foldModel([rule("r", { ...run(condition), must_after: ["a"] })], workflows);
    expect(model.continuations[0]).toMatchObject({ fromWorkflowId: null, fromLabel: "from any workflow" });
    expect(model.chains).toHaveLength(3);
  });

  it("does not infer links from non-run events, inputs or relationships", () => {
    const model = foldModel([rule("r", {
      condition: compare(), trigger: { kind: "event", params: { type: "github.comment.created" } },
      workflow: { id: "b", inputs: { predecessor: "a" } }, must_after: ["a"],
    })], workflows);
    expect(model.entryPoints).toHaveLength(1);
    expect(model.continuations).toEqual([]);
    expect(model.chains).toHaveLength(3);
  });

  it("handles cycles, duplicate links and self links as connected components", () => {
    const model = foldModel([
      rule("ab", run(compare())), rule("ab2", run(compare())),
      rule("ba", { ...run(compare("b")), workflow: { id: "a" } }),
      rule("bb", run(compare("b"))),
    ], workflows);
    expect(model.chains.map((chain) => chain.workflowIds)).toEqual([["a", "b"], ["c"]]);
    expect(model.chains[0].continuations).toHaveLength(4);
  });

  it("retains workflow-less D7 candidates and missing workflow references without writing data", () => {
    const rules = [rule("null", { workflow: null }), rule("absent", { workflow: undefined }),
      rule("missing", { workflow: { id: "missing" }, enabled: false })];
    const before = JSON.stringify(rules);
    const model = foldModel(rules, workflows);
    expect(model.d7Candidates.map((r) => r.id)).toEqual(["null", "absent"]);
    expect(model.workflows.find((wf) => wf.id === "missing")).toMatchObject({
      workflow: null, entries: [{ rule: rules[2], enabled: false }],
    });
    expect(JSON.stringify(rules)).toBe(before);
    expect(foldModel([], []).chains).toEqual([]);
  });
});

describe("sharedValues", () => {
  it("compares JSON objects structurally and excludes identity and lifecycle even when identical", () => {
    const a = rule("a", { description: "same", enabled: true, placement: { machine: "spark", actor: null } });
    const b = rule("b", { description: "same", enabled: true, placement: { actor: null, machine: "spark" } });
    const shared = sharedValues([a, b]);
    expect(shared.placement).toEqual({ kind: "shared", value: a.placement });
    expect(shared.action).toEqual({ kind: "shared", value: { kind: "noop" } });
    for (const field of ["id", "name", "description", "enabled"]) expect(shared).not.toHaveProperty(field);
  });

  it("returns per-rule values for differences, including absent vs null and array order", () => {
    const a = { ...rule("a"), max_attempts: 3, must_after: ["x", "y"] };
    const b = { ...rule("b", { condition: null }), max_attempts: 4, must_after: ["y", "x"] };
    const shared = sharedValues([a, b]);
    expect(shared.max_attempts).toEqual({ kind: "differs", values: [
      { ruleId: "a", value: 3 }, { ruleId: "b", value: 4 },
    ] });
    expect(shared.condition).toEqual({ kind: "differs", values: [
      { ruleId: "a", value: undefined }, { ruleId: "b", value: null },
    ] });
    expect(shared.must_after.kind).toBe("differs");
  });

  it("includes continuation-owned and disabled entries when computing workflow shared values", () => {
    const model = foldModel([rule("a"), rule("b", {
      ...run(compare()), enabled: false, action: { kind: "message" },
    })], workflows);
    expect(model.workflows.find((wf) => wf.id === "b")?.shared.action.kind).toBe("differs");
  });

  it("handles empty and single-rule groups without inventing defaults", () => {
    expect(sharedValues([])).toEqual({});
    expect(sharedValues([rule("r")]).action).toEqual({ kind: "shared", value: { kind: "noop" } });
    expect(sharedValues([rule("r")])).not.toHaveProperty("placement");
  });
});
