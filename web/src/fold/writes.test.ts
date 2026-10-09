import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Condition, Rule } from "../api/types";
import { createFakeApi, fetchFor, type FakeApi } from "../rules/fake-api";
import { createD7Workflow, saveSharedEdit, savePredecessor, type SharedRuleEdit } from "./writes";

const rule = (id: string): Rule => ({
  id, name: id, enabled: true,
  trigger: { kind: "event", params: { type: "rules.run.succeeded" } },
  action: { kind: "message", params: { text: "trigger.data.message" } },
});
const compare = (id: string): Condition => ({
  op: "compare", cmp: "==", left: { field: "data.workflow_id" }, right: { literal: id },
});
const failure = { status: 503, code: "unavailable", message: "Try again" };
let api: FakeApi;
beforeEach(() => {
  api = createFakeApi();
  api.rules = [rule("one"), rule("two"), rule("three")];
  vi.stubGlobal("fetch", fetchFor(api));
});
afterEach(() => vi.unstubAllGlobals());
const requests = () => api.calls.map(({ method, path }) => `${method} ${path}`);

describe("shared edits", () => {
  it("saves rule by rule, returns failures with the old value, and supports retry", async () => {
    const snapshots = structuredClone(api.rules);
    api.failNext["PUT /rules/two"] = failure;
    const action = { kind: "noop" };
    const results = await saveSharedEdit(snapshots, { action });
    expect(results.map((r) => r.status)).toEqual(["saved", "failed", "saved"]);
    expect(results[1]).toMatchObject({ ruleId: "two", snapshot: snapshots[1], attempted: { action }, error: failure });
    expect(api.rules.map((r) => r.action)).toEqual([action, snapshots[1].action, action]);
    expect(requests()).toEqual([
      "GET /rules/one", "PUT /rules/one", "GET /rules/two", "PUT /rules/two",
      "GET /rules/three", "PUT /rules/three",
    ]);
    expect((await saveSharedEdit([snapshots[1]], { action }))[0].status).toBe("saved");
    expect(snapshots[0].action.kind).toBe("message");
  });

  it("never PUTs a rule changed since the edit snapshot, and flags its current value", async () => {
    const snapshots = structuredClone(api.rules);
    api.rules[1].action.params = { text: "someone else's edit" };
    const current = structuredClone(api.rules[1]);
    const results = await saveSharedEdit(snapshots, { action: { kind: "noop" } });
    expect(results.map((r) => r.status)).toEqual(["saved", "skipped-changed", "saved"]);
    expect(results[1]).toMatchObject({ snapshot: snapshots[1], current });
    expect(api.rules[1]).toEqual(current);
    expect(requests()).toContain("GET /rules/two");
    expect(requests()).not.toContain("PUT /rules/two");
  });

  it("compares the full document, including unknown fields, but ignores object key order", async () => {
    const snapshots = structuredClone(api.rules);
    api.rules[0] = Object.fromEntries(Object.entries(api.rules[0]).reverse()) as unknown as Rule;
    Object.assign(api.rules[1], { priority: 17 });
    const results = await saveSharedEdit(snapshots, { condition: null });
    expect(results.map((r) => r.status)).toEqual(["saved", "skipped-changed", "saved"]);
  });

  it("does not PUT after a failed re-read, and still processes later rules", async () => {
    api.failNext["GET /rules/one"] = failure;
    const results = await saveSharedEdit(structuredClone(api.rules), { action: { kind: "noop" } });
    expect(results[0]).toMatchObject({ status: "failed", phase: "read", error: failure });
    expect(requests()).not.toContain("PUT /rules/one");
    expect(results[2].status).toBe("saved");
  });

  it("preserves identity, lifecycle and unedited fields on each rule", async () => {
    api.rules[1].enabled = false;
    Object.assign(api.rules[0], { concurrency_key: "keep", priority: 4 });
    const snapshots = structuredClone(api.rules);
    await saveSharedEdit(snapshots, { action: { kind: "noop" } });
    expect(api.rules).toEqual(snapshots.map((r) => ({ ...r, action: { kind: "noop" } })));
  });

  it("re-reads each rule just before its write, including changes made during fan-out", async () => {
    const snapshots = structuredClone(api.rules);
    const fakeFetch = fetchFor(api);
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const response = await fakeFetch(input, init);
      if (String(input) === "/api/rules/one" && init?.method === "PUT") {
        api.rules[1].description = "Changed while the first rule saved";
      }
      return response;
    });
    const results = await saveSharedEdit(snapshots, { action: { kind: "noop" } });
    expect(results.map((r) => r.status)).toEqual(["saved", "skipped-changed", "saved"]);
    expect(requests()).not.toContain("PUT /rules/two");
    expect(api.rules[1].description).toBe("Changed while the first rule saved");
  });

  it("rejects identity fields at runtime before making any request", async () => {
    const results = await saveSharedEdit(api.rules, { name: "Shared name" } as unknown as SharedRuleEdit);
    expect(results.every((r) => r.status === "failed" && r.phase === "prepare")).toBe(true);
    expect(api.calls).toEqual([]);
  });

  it("encodes rule ids for both re-read and PUT", async () => {
    api.rules = [rule("entry/with space")];
    expect((await saveSharedEdit(api.rules, { action: { kind: "noop" } }))[0].status).toBe("saved");
    expect(requests()).toEqual(["GET /rules/entry%2Fwith%20space", "PUT /rules/entry%2Fwith%20space"]);
  });
});

describe("D7", () => {
  it("creates an empty workflow before pointing the unchanged rule at it", async () => {
    const snapshot = structuredClone(api.rules[0]);
    const result = await createD7Workflow(snapshot, { id: "wrapper", name: "Wrapper" });
    expect(result).toMatchObject({ status: "saved", workflow: { id: "wrapper", steps: [] } });
    expect(api.rules[0]).toEqual({ ...snapshot, workflow: { id: "wrapper" } });
    expect(api.workflows.find((w) => w.id === "wrapper")).toMatchObject({ steps: [], edges: [] });
    expect(requests()).toEqual(["POST /workflows", "GET /rules/one", "PUT /rules/one"]);
  });

  it("deletes the workflow if the rule PUT fails", async () => {
    const snapshot = structuredClone(api.rules[0]);
    api.failNext["PUT /rules/one"] = failure;
    const result = await createD7Workflow(snapshot, { id: "wrapper", name: "Wrapper" });
    expect(result).toMatchObject({ status: "failed", cleanup: "deleted", ruleResult: { phase: "write", error: failure } });
    expect(api.workflows.some((w) => w.id === "wrapper")).toBe(false);
    expect(api.workflowTrash.some((w) => w.id === "wrapper")).toBe(true);
    expect(api.rules[0]).toEqual(snapshot);
    expect(requests().at(-1)).toBe("DELETE /workflows/wrapper");
  });

  it("returns an explicit orphan and both errors when rollback fails", async () => {
    api.failNext["PUT /rules/one"] = failure;
    api.failNext["DELETE /workflows/wrapper"] = { ...failure, message: "Cleanup failed" };
    const result = await createD7Workflow(structuredClone(api.rules[0]), { id: "wrapper", name: "Wrapper" });
    expect(result).toMatchObject({ status: "failed", cleanup: "orphan", orphan: { id: "wrapper" },
      cleanupError: { message: "Cleanup failed" }, ruleResult: { error: failure } });
    expect(api.workflows.some((w) => w.id === "wrapper")).toBe(true);
  });

  it("cleans up when a concurrent rule edit prevents attaching the workflow", async () => {
    const snapshot = structuredClone(api.rules[0]);
    api.rules[0].name = "Changed";
    const result = await createD7Workflow(snapshot, { id: "wrapper", name: "Wrapper" });
    expect(result).toMatchObject({ status: "skipped-changed", cleanup: "deleted" });
    expect(requests()).not.toContain("PUT /rules/one");
    expect(api.workflows.some((w) => w.id === "wrapper")).toBe(false);
  });

  it("does not touch rules or delete an existing workflow if creation fails", async () => {
    const existing = structuredClone(api.workflows[0]);
    const result = await createD7Workflow(structuredClone(api.rules[0]), existing);
    expect(result).toMatchObject({ status: "failed", phase: "create", error: { status: 409 } });
    expect(requests()).toEqual(["POST /workflows"]);
    expect(api.workflows[0]).toEqual(existing);
  });

  it("rolls back if the rule cannot be re-read", async () => {
    api.failNext["GET /rules/one"] = failure;
    const result = await createD7Workflow(api.rules[0], { id: "wrapper", name: "Wrapper" });
    expect(result).toMatchObject({ status: "failed", cleanup: "deleted", ruleResult: { phase: "read" } });
    expect(requests()).toEqual(["POST /workflows", "GET /rules/one", "DELETE /workflows/wrapper"]);
  });

  it("rejects an already attached rule before creating anything", async () => {
    api.rules[0].workflow = { id: "existing" };
    expect(await createD7Workflow(api.rules[0], { id: "wrapper", name: "Wrapper" }))
      .toMatchObject({ status: "failed", phase: "prepare" });
    expect(api.calls).toEqual([]);
  });
});

describe("continuation predecessor", () => {
  it("rewrites only the all-term workflow compare, preserving every other field", async () => {
    const unrelated: Condition = { op: "or", args: [compare("alternative"), { op: "exists", arg: { field: "data.result" } }] };
    api.rules[0].condition = { op: "and", args: [
      unrelated, { op: "and", args: [compare("old"), { op: "exists", arg: { var: "ready" } }] },
    ] };
    Object.assign(api.rules[0], { concurrency_key: "keep", counts_toward_budget: false });
    const snapshot = structuredClone(api.rules[0]);
    const expected = structuredClone(snapshot);
    if (expected.condition?.op === "and" && expected.condition.args[1].op === "and") {
      expected.condition.args[1].args[0] = compare("new");
    }
    expect((await savePredecessor(snapshot, "new")).status).toBe("saved");
    expect(api.rules[0]).toEqual(expected);
    expect(snapshot.condition).not.toEqual(expected.condition);
  });

  it("handles a bare compare and reversed operands", async () => {
    api.rules[0].condition = { op: "compare", cmp: "==", left: { literal: "old" }, right: { field: "data.workflow_id" } };
    await savePredecessor(structuredClone(api.rules[0]), "new");
    expect(api.rules[0].condition).toEqual({ op: "compare", cmp: "==", left: { literal: "new" }, right: { field: "data.workflow_id" } });
  });

  it.each<Condition>([
    { op: "or", args: [compare("old"), compare("other")] },
    { op: "not", arg: compare("old") },
    { op: "and", args: [compare("old"), compare("other")] },
    { ...compare("old"), cmp: "!=" } as Condition,
  ])("rejects missing or ambiguous all-term links without writing: %j", async (condition) => {
    api.rules[0].condition = condition;
    const result = await savePredecessor(structuredClone(api.rules[0]), "new");
    expect(result).toMatchObject({ status: "failed", phase: "prepare" });
    expect(api.calls).toEqual([]);
  });

  it("does not overwrite a concurrently changed continuation", async () => {
    api.rules[0].condition = compare("old");
    const snapshot = structuredClone(api.rules[0]);
    api.rules[0].condition = compare("someone-else");
    expect((await savePredecessor(snapshot, "new")).status).toBe("skipped-changed");
    expect(requests()).toEqual(["GET /rules/one"]);
  });
});
