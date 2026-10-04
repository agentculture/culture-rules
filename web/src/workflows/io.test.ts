import { describe, expect, it } from "vitest";
import type { WorkflowDef } from "../api/workflows";
import { REVIEW_PR } from "./fixture";
import {
  addInput,
  addOutput,
  addVariable,
  ioNameProblem,
  outputSourceOptions,
  outputSourceProblem,
  patchInput,
  patchOutput,
  patchVariable,
  removeInput,
  removeOutput,
  removeVariable,
  renameInput,
  renameOutput,
  renameVariable,
} from "./io";

const WITH_VARS: WorkflowDef = {
  ...REVIEW_PR,
  variables: [{ name: "attempts", type: "integer", default: 0 }],
  outputs: [
    ...(REVIEW_PR.outputs ?? []),
    { name: "tries", type: "integer", source: "vars.attempts" },
    { name: "which", type: "string", source: "inputs.repo" },
  ],
};

const sourceOf = (wf: WorkflowDef, name: string) => wf.outputs!.find((o) => o.name === name)!.source;

describe("io names", () => {
  it("refuses an empty, dotted/spaced or taken name, with a guidance code", () => {
    expect(ioNameProblem(["pr", "repo"], "")).toBe("empty");
    expect(ioNameProblem(["pr", "repo"], "a.b")).toBe("unsafe_id");
    expect(ioNameProblem(["pr", "repo"], "a b")).toBe("unsafe_id");
    expect(ioNameProblem(["pr", "repo"], "repo")).toBe("duplicate");
    expect(ioNameProblem(["pr", "repo"], "branch_2")).toBeNull();
  });
});

describe("workflow inputs", () => {
  it("adds a fresh, uniquely named input", () => {
    const { workflow, name } = addInput(REVIEW_PR);
    expect(name).toBe("input1");
    expect(workflow.inputs!.at(-1)).toEqual({ name: "input1", type: "any" });
    expect(addInput(workflow).name).toBe("input2");
  });

  it("renaming an input follows every wire and output source that reads it", () => {
    const next = renameInput(WITH_VARS, "repo", "repository");
    expect(next.inputs!.map((p) => p.name)).toEqual(["pr", "repository"]);
    const fromRepo = next.edges!.filter((e) => e.source === "inputs").map((e) => e.source_port);
    expect(fromRepo).toEqual(["pr", "repository", "repository"]);
    expect(sourceOf(next, "which")).toBe("inputs.repository");
    expect(REVIEW_PR.inputs!.map((p) => p.name)).toEqual(["pr", "repo"]); // never mutated
  });

  it("removing an input drops its wires and unsets output sources that read it", () => {
    const next = removeInput(WITH_VARS, "repo");
    expect(next.inputs!.map((p) => p.name)).toEqual(["pr"]);
    expect(next.edges!.some((e) => e.source === "inputs" && e.source_port === "repo")).toBe(false);
    expect(sourceOf(next, "which")).toBeNull();
  });

  it("a type change drops wires that no longer fit", () => {
    const next = patchInput(WITH_VARS, "repo", { type: "boolean" });
    expect(next.inputs!.find((p) => p.name === "repo")!.type).toBe("boolean");
    expect(next.edges!.some((e) => e.source === "inputs" && e.source_port === "repo")).toBe(false);
    expect(sourceOf(next, "which")).toBeNull();
    const described = patchInput(WITH_VARS, "pr", { description: "PR number", required: false });
    expect(described.inputs![0]).toEqual({ name: "pr", type: "integer", description: "PR number", required: false });
  });
});

describe("workflow outputs", () => {
  it("adds, renames and removes outputs", () => {
    const { workflow, name } = addOutput(REVIEW_PR);
    expect(name).toBe("output1");
    expect(workflow.outputs!.at(-1)).toEqual({ name: "output1", type: "any", source: null });
    const renamed = renameOutput(REVIEW_PR, "verdict", "result");
    expect(renamed.outputs![0]).toEqual({ name: "result", type: "string", source: "steps.decide.outputs.verdict" });
    expect(removeOutput(REVIEW_PR, "owner").outputs!.map((o) => o.name)).toEqual(["verdict"]);
  });

  it("a type change unsets a source that no longer fits", () => {
    expect(sourceOf(patchOutput(REVIEW_PR, "verdict", { type: "boolean" }), "verdict")).toBeNull();
    expect(sourceOf(patchOutput(REVIEW_PR, "verdict", { type: "any" }), "verdict")).toBe(
      "steps.decide.outputs.verdict",
    );
  });

  it("offers inputs, variables and step output ports whose type fits", () => {
    const options = outputSourceOptions(WITH_VARS, "tries");
    const values = options.map((o) => o.value);
    expect(values).toContain("inputs.pr");
    expect(values).toContain("vars.attempts");
    expect(values).not.toContain("inputs.repo");
    expect(values).not.toContain("steps.decide.outputs.verdict");
    const verdict = outputSourceOptions(WITH_VARS, "verdict");
    expect(verdict.find((o) => o.value === "steps.decide.outputs.verdict")!.label).toBe("Decide · verdict");
    expect(verdict.find((o) => o.value === "inputs.repo")!.label).toBe("Inputs · repo");
  });

  it("checks a typed source reference: shape, target and type", () => {
    expect(outputSourceProblem(WITH_VARS, "verdict", "")).toBeNull();
    expect(outputSourceProblem(WITH_VARS, "verdict", "steps.decide.outputs.verdict")).toBeNull();
    expect(outputSourceProblem(WITH_VARS, "verdict", "steps.decide")).toBe("invalid_reference");
    expect(outputSourceProblem(WITH_VARS, "verdict", "steps.nope.outputs.x")).toBe("invalid_reference");
    expect(outputSourceProblem(WITH_VARS, "verdict", "inputs.pr")).toBe("output_type_mismatch");
  });
});

describe("workflow variables", () => {
  it("adds, retypes and sets defaults", () => {
    const { workflow, name } = addVariable(REVIEW_PR);
    expect(name).toBe("var1");
    expect(workflow.variables).toEqual([{ name: "var1", type: "any" }]);
    const typed = patchVariable(workflow, "var1", { type: "object", default: { a: 1 } });
    expect(typed.variables).toEqual([{ name: "var1", type: "object", default: { a: 1 } }]);
  });

  it("renaming a variable follows output sources; removing one unsets them", () => {
    expect(sourceOf(renameVariable(WITH_VARS, "attempts", "tries_so_far"), "tries")).toBe("vars.tries_so_far");
    const gone = removeVariable(WITH_VARS, "attempts");
    expect(gone.variables).toEqual([]);
    expect(sourceOf(gone, "tries")).toBeNull();
  });

  it("a variable retyped out of an output's type unsets that output's source", () => {
    expect(sourceOf(patchVariable(WITH_VARS, "attempts", { type: "string" }), "tries")).toBeNull();
  });
});
