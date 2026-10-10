import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { Condition, Rule, Workflow } from "../api/types";
import { EXCLUDED_RULE_FIELDS, SHAREABLE_RULE_FIELDS, foldModel, predecessorTerms, sharedValues } from "./model";

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
    // #35: the trigger rules queue the PR (queue-add); the queue moves (queue-progress) and
    // pr-fixer-dispatch starts pr-fix from the queue's dispatch event; d34: a /stop or a
    // thumbs-down runs queue-stop, after which the queue moves too
    expect(model.workflows).toHaveLength(7);
    expect(model.entryPoints).toHaveLength(11);
    expect(model.continuations).toHaveLength(11); // d37: pr-fixer-retry-failed
    expect(model.chains.map((chain) => chain.workflowIds.slice().sort())).toEqual([
      ["pr-fix", "publish-fix", "queue-add", "queue-progress", "queue-stop", "review-commit"],
      ["report-secrets"],
    ]);
    expect(model.workflows.find((wf) => wf.id === "pr-fix")?.entries.map((entry) => entry.rule.id))
      .toEqual(["pr-fixer-dispatch"]);
    expect(model.workflows.find((wf) => wf.id === "queue-add")?.entries.map((entry) => entry.rule.id))
      .toContain("pr-fixer-refix");
    expect(model.workflows.find((wf) => wf.id === "report-secrets")?.entries).toHaveLength(2);
    expect(model.workflows.find((wf) => wf.id === "queue-stop")?.entries.map((entry) => entry.rule.id))
      .toEqual(["pr-fixer-stop-reaction", "pr-fixer-stop"]);
    expect(model.workflows.flatMap((wf) => wf.entries)).toHaveLength(22);
    expect(model.continuations.map((entry) => [entry.rule.id, entry.fromWorkflowId, entry.workflowId]))
      .toEqual([
        ["pr-fixer-publish", "review-commit", "publish-fix"],
        ["pr-fixer-queue-progress-cancelled", "pr-fix", "queue-progress"],
        ["pr-fixer-queue-progress-failed", "pr-fix", "queue-progress"],
        ["pr-fixer-queue-progress-fixed", "pr-fix", "queue-progress"],
        ["pr-fixer-queue-progress-stopped", "queue-stop", "queue-progress"],
        ["pr-fixer-queue-progress-superseded", "pr-fix", "queue-progress"],
        ["pr-fixer-queue-progress", "queue-add", "queue-progress"],
        ["pr-fixer-refix", "review-commit", "queue-add"],
        ["pr-fixer-retry-failed", "pr-fix", "queue-add"],
        ["pr-fixer-retry", "pr-fix", "queue-add"],
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

  it.each([["a", "a"], ["a", "c"]])("marks multiple predecessor terms ambiguous: %j", (...ids) => {
    const terms = ids.map((id) => compare(id));
    const condition: Condition = { op: "and", args: [terms[0], { op: "and", args: [terms[1]] }] };
    expect(predecessorTerms(condition)).toEqual(terms);
    const model = foldModel([rule("r", run(condition))], workflows);
    expect(model.continuations[0]).toMatchObject({
      predecessor: { kind: "ambiguous", workflowIds: ids },
      fromWorkflowId: null, fromLabel: "from multiple workflow terms",
    });
    expect(model.chains).toHaveLength(3);
  });

  it.each([
    compare(""),
    { op: "compare", cmp: "==", left: { literal: "" }, right: { field: "data.workflow_id" } } as Condition,
  ])("accepts an empty string predecessor literal: %j", (condition) => {
    expect(predecessorTerms(condition)).toEqual([condition]);
    expect(foldModel([rule("r", run(condition))], workflows).continuations[0]).toMatchObject({
      predecessor: { kind: "linked", workflowId: "" }, fromWorkflowId: "", fromLabel: "from ",
    });
  });

  it("treats an empty workflow reference as a D7 candidate", () => {
    const candidate = rule("empty", { workflow: { id: "" } });
    const model = foldModel([candidate], workflows);
    expect(model.d7Candidates).toEqual([candidate]);
    expect(model.workflows.map((workflow) => workflow.id)).toEqual(["a", "b", "c"]);
    expect(model.entryPoints).toEqual([]);
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
  it("excludes schema and server metadata even when values differ", () => {
    const a = { ...rule("r"), schema_version: "1.0", updated_at: "first",
      deleted_at: null, deleted_by: null, restorable_until: null };
    const b = { ...a, schema_version: "2.0", updated_at: "second" };
    const shared = sharedValues([a, b]);
    expect(EXCLUDED_RULE_FIELDS).toEqual([
      "id", "name", "description", "enabled", "schema_version", "updated_at",
      "deleted_at", "deleted_by", "restorable_until",
    ]);
    for (const field of EXCLUDED_RULE_FIELDS) expect(shared).not.toHaveProperty(field);
  });

  it("exports the D3-D6 workflow presentation fields using stored Rule names", () => {
    expect(SHAREABLE_RULE_FIELDS).toEqual([
      "must_after", "may_after", "supersedes", "exclusive_group", "priority",
      "concurrency_key", "max_attempts", "counts_toward_budget",
      "placement", "action", "on_failure", "condition",
    ]);
  });

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
