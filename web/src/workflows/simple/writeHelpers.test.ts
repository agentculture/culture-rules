import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createFakeApi, fetchFor, type FakeApi } from "../../rules/fake-api";
import { createRuleDoc, deleteWorkflowDoc } from "../../fold/writes";
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
