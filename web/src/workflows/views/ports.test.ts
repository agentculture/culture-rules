import { describe, expect, it } from "vitest";
import type { WorkflowDef } from "../../api/workflows";
import { REVIEW_PR } from "../fixture";
import { debugColumns, debugPorts, portLinks, portRef, unboundRequired } from "./ports";

describe("the Debug view's port model", () => {
  it("names a port by the reference a definition would use", () => {
    expect(portRef("inputs", "out", "repo")).toBe("inputs.repo");
    expect(portRef("outputs", "in", "verdict")).toBe("outputs.verdict");
    expect(portRef("review", "in", "diff")).toBe("steps.review.inputs.diff");
    expect(portRef("review", "out", "findings")).toBe("steps.review.outputs.findings");
  });

  it("lists every input and output port with its type and what it reads", () => {
    const ports = debugPorts(REVIEW_PR);
    const refs = ports.map((p) => p.ref);
    // 2 workflow inputs, 4 steps' 6 inputs and 5 outputs, 2 workflow outputs
    expect(refs).toHaveLength(2 + 6 + 5 + 2);
    expect(new Set(refs).size).toBe(refs.length);
    const byRef = new Map(ports.map((p) => [p.ref, p]));
    expect(byRef.get("inputs.pr")).toMatchObject({ type: "integer", reads: null, exported: false });
    expect(byRef.get("steps.fetch-diff.inputs.pr")).toMatchObject({ type: "integer", reads: "inputs.pr" });
    expect(byRef.get("steps.review.inputs.diff")).toMatchObject({
      type: "string",
      reads: "steps.fetch-diff.outputs.diff",
    });
    expect(byRef.get("steps.review.outputs.owner")).toMatchObject({ type: "string", exported: true });
    expect(byRef.get("outputs.verdict")).toMatchObject({ type: "string", reads: "steps.decide.outputs.verdict" });
  });

  it("shows a port with no type as any, and an unwired input as reading nothing", () => {
    const wf: WorkflowDef = {
      id: "w",
      name: "w",
      steps: [{ id: "a", kind: "code", inputs: [{ name: "x" }], outputs: [] }],
      outputs: [{ name: "v", source: "vars.limit" }],
      variables: [{ name: "limit", type: "integer" }],
    };
    const byRef = new Map(debugPorts(wf).map((p) => [p.ref, p]));
    expect(byRef.get("steps.a.inputs.x")).toMatchObject({ type: "any", reads: null });
    expect(byRef.get("outputs.v")).toMatchObject({ reads: "vars.limit" });
  });

  it("puts each step in the column after everything it reads", () => {
    const cols = debugColumns(REVIEW_PR).map((c) => c.map((g) => g.id));
    expect(cols).toEqual([["inputs"], ["fetch-diff", "run-tests"], ["review"], ["decide"], ["outputs"]]);
  });

  it("links a step output upstream to its step's inputs and their sources", () => {
    const links = portLinks(REVIEW_PR, "steps.review.outputs.findings");
    expect([...links.upstream].sort()).toEqual(
      ["steps.fetch-diff.outputs.diff", "steps.review.inputs.diff"].sort(),
    );
    expect([...links.downstream]).toEqual(["steps.decide.inputs.findings"]);
  });

  it("links a step input upstream to its source and downstream to its step's outputs and their readers", () => {
    const links = portLinks(REVIEW_PR, "steps.review.inputs.diff");
    expect([...links.upstream]).toEqual(["steps.fetch-diff.outputs.diff"]);
    expect([...links.downstream].sort()).toEqual(
      [
        "steps.review.outputs.findings",
        "steps.review.outputs.owner",
        "steps.decide.inputs.findings",
        "outputs.owner",
      ].sort(),
    );
  });

  it("links a workflow input to every port that reads it, and an output to its source", () => {
    expect([...portLinks(REVIEW_PR, "inputs.repo").downstream].sort()).toEqual(
      ["steps.fetch-diff.inputs.repo", "steps.run-tests.inputs.repo"].sort(),
    );
    expect([...portLinks(REVIEW_PR, "inputs.repo").upstream]).toEqual([]);
    expect([...portLinks(REVIEW_PR, "outputs.verdict").upstream]).toEqual(["steps.decide.outputs.verdict"]);
  });

  it("follows everything downstream on request", () => {
    const all = portLinks(REVIEW_PR, "steps.fetch-diff.outputs.diff", { everything: true }).downstream;
    expect([...all].sort()).toEqual(
      [
        "steps.review.inputs.diff",
        "steps.review.outputs.findings",
        "steps.review.outputs.owner",
        "steps.decide.inputs.findings",
        "steps.decide.outputs.verdict",
        "outputs.owner",
        "outputs.verdict",
      ].sort(),
    );
  });

  it("counts required step inputs that read nothing", () => {
    expect(unboundRequired(REVIEW_PR)).toEqual([]);
    const wf: WorkflowDef = {
      ...REVIEW_PR,
      steps: [
        ...(REVIEW_PR.steps ?? []),
        { id: "late", kind: "code", inputs: [{ name: "need", type: "string", required: true }], outputs: [] },
      ],
    };
    expect(unboundRequired(wf)).toEqual(["steps.late.inputs.need"]);
  });

  it("shows a loop's body ports, filled by name from the loop and feeding its outputs", () => {
    const wf: WorkflowDef = {
      id: "fix",
      name: "fix",
      inputs: [{ name: "repo", type: "string" }],
      steps: [
        {
          id: "fix",
          kind: "retry_until",
          max_iterations: 3,
          inputs: [{ name: "repo", type: "string" }],
          outputs: [{ name: "verdict", type: "string" }],
          body: [
            {
              id: "agent",
              kind: "ai",
              inputs: [{ name: "repo", type: "string" }, { name: "iteration", type: "integer" }],
              outputs: [{ name: "sha", type: "string" }],
            },
            {
              id: "gate",
              kind: "code",
              inputs: [{ name: "commit", type: "string" }],
              outputs: [{ name: "verdict", type: "string" }],
            },
          ],
        },
      ],
      edges: [
        { source: "inputs", source_port: "repo", target: "fix", target_port: "repo" },
        { source: "agent", source_port: "sha", target: "gate", target_port: "commit" },
      ],
      outputs: [{ name: "verdict", type: "string", source: "steps.fix.outputs.verdict" }],
    };
    const byRef = new Map(debugPorts(wf).map((p) => [p.ref, p]));
    expect(byRef.get("steps.agent.inputs.repo")).toMatchObject({ reads: "steps.fix.inputs.repo", byName: true });
    expect(byRef.get("steps.agent.inputs.iteration")).toMatchObject({ byName: true });
    expect(byRef.get("steps.gate.inputs.commit")).toMatchObject({ reads: "steps.agent.outputs.sha" });
    expect(debugColumns(wf)[1][0].body.map((g) => g.id)).toEqual(["agent", "gate"]);
    expect(unboundRequired(wf)).toEqual([]);
    expect([...portLinks(wf, "steps.gate.outputs.verdict").downstream]).toEqual(["steps.fix.outputs.verdict"]);
    expect(portLinks(wf, "inputs.repo", { everything: true }).downstream).toContain("outputs.verdict");
  });

  describe("loops, as culture_rules/engine/runs.py runs them", () => {
    const fixer: WorkflowDef = {
      id: "pr-fix",
      name: "pr-fix",
      inputs: [
        { name: "instruction", type: "string" },
        { name: "base_sha", type: "string" },
      ],
      steps: [
        {
          id: "fix",
          kind: "retry_until",
          max_iterations: 3,
          config: { carry: { instruction: "instruction" } },
          inputs: [
            { name: "instruction", type: "string" },
            { name: "base_sha", type: "string" },
            { name: "index", type: "integer" },
          ],
          outputs: [{ name: "verdict", type: "string" }],
          body: [
            {
              id: "agent",
              kind: "ai",
              inputs: [
                { name: "instruction", type: "string" },
                { name: "index", type: "integer" },
              ],
              outputs: [
                { name: "worktree", type: "string" },
                { name: "verdict", type: "string" },
              ],
            },
            {
              id: "gate",
              kind: "code",
              inputs: [
                { name: "worktree", type: "string" },
                { name: "base", type: "string" },
              ],
              outputs: [
                { name: "verdict", type: "string" },
                { name: "instruction", type: "string" },
              ],
            },
          ],
        },
      ],
      edges: [
        { source: "inputs", source_port: "instruction", target: "fix", target_port: "instruction" },
        { source: "inputs", source_port: "base_sha", target: "fix", target_port: "base_sha" },
        { source: "fix", source_port: "base_sha", target: "gate", target_port: "base" },
        { source: "agent", source_port: "worktree", target: "gate", target_port: "worktree" },
      ],
      outputs: [{ name: "verdict", type: "string", source: "steps.fix.outputs.verdict" }],
    };
    const byRef = new Map(debugPorts(fixer).map((p) => [p.ref, p]));

    it("an edge from the parent loop reads the loop's input, not an output", () => {
      expect(byRef.get("steps.gate.inputs.base")).toMatchObject({ reads: "steps.fix.inputs.base_sha", byName: false });
      expect([...portLinks(fixer, "steps.fix.inputs.base_sha").downstream]).toContain("steps.gate.inputs.base");
      expect([...portLinks(fixer, "steps.gate.inputs.base").upstream]).toContain("steps.fix.inputs.base_sha");
    });

    it("a carried input takes the previous try's result field", () => {
      expect(byRef.get("steps.agent.inputs.instruction")).toMatchObject({
        reads: "steps.fix.inputs.instruction",
        byName: true,
        carried: "steps.gate.outputs.instruction",
      });
      expect([...portLinks(fixer, "steps.gate.outputs.instruction").downstream]).toContain(
        "steps.agent.inputs.instruction",
      );
      expect([...portLinks(fixer, "steps.agent.inputs.instruction").upstream]).toContain(
        "steps.gate.outputs.instruction",
      );
    });

    it("index and iteration are the engine's, over a loop input of the same name", () => {
      expect(byRef.get("steps.agent.inputs.index")).toMatchObject({ reads: "loop.index", byName: true });
      expect([...portLinks(fixer, "steps.fix.inputs.index").downstream]).not.toContain("steps.agent.inputs.index");
    });

    it("a retry loop's outputs come only from its final body step", () => {
      const up = [...portLinks(fixer, "steps.fix.outputs.verdict").upstream];
      expect(up).toContain("steps.gate.outputs.verdict");
      expect(up).not.toContain("steps.agent.outputs.verdict");
    });

    it("a for_each loop's results gather every output of its final body step", () => {
      const wf: WorkflowDef = {
        id: "each",
        name: "each",
        steps: [
          {
            id: "each",
            kind: "for_each",
            max_iterations: 5,
            inputs: [{ name: "items", type: "array" }],
            outputs: [
              { name: "results", type: "array" },
              { name: "a", type: "array" },
            ],
            body: [
              {
                id: "one",
                kind: "code",
                inputs: [{ name: "item" }, { name: "iteration", type: "integer" }],
                outputs: [{ name: "a" }, { name: "b" }],
              },
            ],
          },
        ],
        edges: [],
        outputs: [],
      };
      const ports = new Map(debugPorts(wf).map((p) => [p.ref, p]));
      expect(ports.get("steps.one.inputs.item")).toMatchObject({ reads: "loop.item", byName: true });
      expect(ports.get("steps.one.inputs.iteration")).toMatchObject({ reads: "loop.iteration" });
      expect([...portLinks(wf, "steps.each.outputs.results").upstream]).toEqual(
        expect.arrayContaining(["steps.one.outputs.a", "steps.one.outputs.b"]),
      );
      const a = [...portLinks(wf, "steps.each.outputs.a").upstream];
      expect(a).toContain("steps.one.outputs.a");
      expect(a).not.toContain("steps.one.outputs.b");
    });
  });
});
