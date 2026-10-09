import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createFakeApi, fetchFor, type FakeApi } from "../../rules/fake-api";
import { createD7Workflow, createRuleDoc, deleteUnusedWorkflow, deleteWorkflowDoc, saveSharedEdit } from "../../fold/writes";
import { handle } from "../../rules/fake-api";
import { runKeyProblem } from "./text";

let api: FakeApi;
beforeEach(() => {
  api = createFakeApi();
  vi.stubGlobal("fetch", fetchFor(api));
});
afterEach(() => vi.unstubAllGlobals());

describe("fold writes: create a rule, delete a workflow", () => {
  it("createRuleDoc answers the stored rule, or the failure", async () => {
    const doc = { id: "x", name: "X", trigger: { kind: "manual" }, action: { kind: "noop" } };
    const made = await createRuleDoc(doc);
    expect(made).toMatchObject({ status: "saved", rule: { id: "x" } });
    expect(api.calls.at(-1)).toMatchObject({ method: "POST", path: "/rules", body: doc });
    const again = await createRuleDoc(doc);
    expect(again).toMatchObject({ status: "failed", error: { status: 409 } });
  });

  it("deleteWorkflowDoc answers deleted, or the failure", async () => {
    expect(await deleteWorkflowDoc("build-image")).toEqual({ status: "deleted", workflowId: "build-image" });
    expect(await deleteWorkflowDoc("build-image")).toMatchObject({ status: "failed", workflowId: "build-image", error: { status: 404 } });
  });
});

describe("run key templates, as validate.py checks them", () => {
  it.each([
    ["pr-fixer:{trigger.data.repository}#{trigger.data.number}", null],
    ["plain", null],
    ["{{literal}} {trigger.a}", null],
    ["", /empty/],
    ["  ", /empty/],
    ["{vars.x}", /only \{trigger.<path>\} placeholders/],
    ["{trigger}", /only \{trigger.<path>\} placeholders/],
    ["{}", /only \{trigger.<path>\} placeholders/],
    ["{trigger.a:>10}", /format specs/],
    ["{trigger.a!r}", /format specs/],
    ["{trigger.a", /unbalanced/],
    ["trigger.a}", /unbalanced/],
    ["{trigger.{a}}", /unbalanced/],
  ])("%j", (template, problem) => {
    const found = runKeyProblem(template);
    if (problem === null) expect(found).toBeNull();
    else expect(found).toMatch(problem);
  });
});

describe("a write that changes nothing is judged after the re-read (c27)", () => {
  it("unchanged when the stored rule equals the snapshot and already holds the edit: no PUT", async () => {
    handle(api, "GET", "/rules", new URLSearchParams());
    const snapshot = structuredClone(api.rules.find((r) => r.id === "train-batch")!);
    api.calls = [];
    const [result] = await saveSharedEdit([snapshot], { placement: snapshot.placement });
    expect(result.status).toBe("unchanged");
    expect(api.calls.map((c) => `${c.method} ${c.path}`)).toEqual(["GET /rules/train-batch"]);
  });

  it("skipped-changed when the snapshot holds the edit but the stored rule changed meanwhile", async () => {
    handle(api, "GET", "/rules", new URLSearchParams());
    const snapshot = structuredClone(api.rules.find((r) => r.id === "train-batch")!);
    Object.assign(api.rules.find((r) => r.id === "train-batch")!, { name: "Renamed", updated_at: "2030-01-01T00:00:00Z" });
    const [result] = await saveSharedEdit([snapshot], { placement: snapshot.placement });
    expect(result.status).toBe("skipped-changed");
  });

  it("a forbidden field still fails at prepare, even when it would change nothing", async () => {
    const snapshot = structuredClone(api.rules[0]);
    const [result] = await saveSharedEdit([snapshot], { name: snapshot.name } as never);
    expect(result).toMatchObject({ status: "failed", phase: "prepare" });
  });
});

describe("deleteUnusedWorkflow re-reads the rules first", () => {
  it("refuses while a rule still points at the workflow", async () => {
    expect(await deleteUnusedWorkflow("build-image")).toEqual({ status: "in-use", workflowId: "build-image", rules: [{ id: "build-and-publish", name: "Build and publish" }] });
    expect(api.calls.some((c) => c.method === "DELETE")).toBe(false);
  });
  it("deletes when no rule uses it", async () => {
    expect(await deleteUnusedWorkflow("review-pr")).toEqual({ status: "deleted", workflowId: "review-pr" });
  });
});

describe("fold-fixA: D7 cleanup never deletes a referenced wrapper", () => {
  const lone = () => ({ id: "lone", name: "Lone", trigger: { kind: "manual" }, action: { kind: "noop" }, enabled: true });
  beforeEach(() => {
    api.rules.push(lone() as never);
    handle(api, "GET", "/rules", new URLSearchParams());
    api.calls = [];
  });
  /** After the wrapper is created, `meanwhile` runs once (another writer), before the next request. */
  function after(createPath: string, meanwhile: () => void) {
    const base = fetchFor(api);
    let done = false;
    vi.stubGlobal("fetch", (async (input: RequestInfo | URL, init?: RequestInit) => {
      const answer = await base(input, init);
      if (!done && (init?.method ?? "GET").toUpperCase() === "POST" && String(input).endsWith(createPath)) {
        done = true;
        meanwhile();
      }
      return answer;
    }) as typeof fetch);
  }

  it("an attach completed by another writer before the second read is reconciled as saved", async () => {
    after("/workflows", () => {
      Object.assign(api.rules.find((r) => r.id === "lone")!, { workflow: { id: "lone-wf" }, updated_at: "2030-01-01T00:00:00Z" });
    });
    const snapshot = structuredClone(api.rules.find((r) => r.id === "lone")!);
    delete (snapshot as { workflow?: unknown }).workflow;
    const result = await createD7Workflow({ ...snapshot }, { id: "lone-wf", name: "Lone" });
    expect(result.status).toBe("saved");
    expect(api.calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("a failed attach keeps the wrapper when another rule has adopted it", async () => {
    api.failNext["PUT /rules/lone"] = { status: 422, code: "invalid_rule", message: "refused" };
    after("/workflows", () => {
      api.rules.push({ ...lone(), id: "adopter", name: "Adopter", workflow: { id: "lone-wf" } } as never);
    });
    const result = await createD7Workflow(structuredClone(api.rules.find((r) => r.id === "lone")!), { id: "lone-wf", name: "Lone" });
    expect(result).toMatchObject({ status: "failed", cleanup: "in-use", usedBy: [{ id: "adopter", name: "Adopter" }] });
    expect(api.calls.some((c) => c.method === "DELETE")).toBe(false);
    expect(api.workflows.some((w) => w.id === "lone-wf")).toBe(true);
  });
});
