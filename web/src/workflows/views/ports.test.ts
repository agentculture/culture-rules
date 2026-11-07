import { describe, expect, it } from "vitest";
import type { WorkflowDef } from "../../api/workflows";
import { SUMMARIZE_DOCUMENT } from "../fixture";
import { allLinks, debugColumns, debugPorts, portLinks, portRef, unboundRequired } from "./ports";

describe("the Debug view's port model", () => {
  it("names a port by the reference a definition would use", () => {
    expect(portRef("inputs", "out", "repo")).toBe("inputs.repo");
    expect(portRef("outputs", "in", "verdict")).toBe("outputs.verdict");
    expect(portRef("review", "in", "diff")).toBe("steps.review.inputs.diff");
    expect(portRef("review", "out", "findings")).toBe("steps.review.outputs.findings");
  });

  it("lists every input and output port with its type and what it reads", () => {
    const ports = debugPorts(SUMMARIZE_DOCUMENT);
    const refs = ports.map((p) => p.ref);
    // 2 workflow inputs, 4 steps' 6 inputs and 5 outputs, 2 workflow outputs
    expect(refs).toHaveLength(2 + 6 + 5 + 2);
    expect(new Set(refs).size).toBe(refs.length);
    const byRef = new Map(ports.map((p) => [p.ref, p]));
    expect(byRef.get("inputs.pr")).toMatchObject({ type: "integer", reads: null, exported: false });
    expect(byRef.get("steps.fetch-text.inputs.pr")).toMatchObject({ type: "integer", reads: "inputs.pr" });
    expect(byRef.get("steps.review.inputs.diff")).toMatchObject({
      type: "string",
      reads: "steps.fetch-text.outputs.diff",
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
    const cols = debugColumns(SUMMARIZE_DOCUMENT).map((c) => c.map((g) => g.id));
    expect(cols).toEqual([["inputs"], ["fetch-text", "check-text"], ["review"], ["decide"], ["outputs"]]);
  });

  it("links a step output upstream to its step's inputs and their sources", () => {
    const links = portLinks(SUMMARIZE_DOCUMENT, "steps.review.outputs.findings");
    expect([...links.upstream].sort()).toEqual(
      ["steps.fetch-text.outputs.diff", "steps.review.inputs.diff"].sort(),
    );
    expect([...links.downstream]).toEqual(["steps.decide.inputs.findings"]);
  });

  it("links a step input upstream to its source and downstream to its step's outputs and their readers", () => {
    const links = portLinks(SUMMARIZE_DOCUMENT, "steps.review.inputs.diff");
    expect([...links.upstream]).toEqual(["steps.fetch-text.outputs.diff"]);
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
    expect([...portLinks(SUMMARIZE_DOCUMENT, "inputs.repo").downstream].sort()).toEqual(
      ["steps.fetch-text.inputs.repo", "steps.check-text.inputs.repo"].sort(),
    );
    expect([...portLinks(SUMMARIZE_DOCUMENT, "inputs.repo").upstream]).toEqual([]);
    expect([...portLinks(SUMMARIZE_DOCUMENT, "outputs.verdict").upstream]).toEqual(["steps.decide.outputs.verdict"]);
  });

  it("follows everything downstream on request", () => {
    const all = portLinks(SUMMARIZE_DOCUMENT, "steps.fetch-text.outputs.diff", { everything: true }).downstream;
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
    expect(unboundRequired(SUMMARIZE_DOCUMENT)).toEqual([]);
    const wf: WorkflowDef = {
      ...SUMMARIZE_DOCUMENT,
      steps: [
        ...(SUMMARIZE_DOCUMENT.steps ?? []),
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
    const processor: WorkflowDef = {
      id: "prepare-document",
      name: "prepare-document",
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
    const byRef = new Map(debugPorts(processor).map((p) => [p.ref, p]));

    it("an edge from the parent loop reads the loop's input, not an output", () => {
      expect(byRef.get("steps.gate.inputs.base")).toMatchObject({ reads: "steps.fix.inputs.base_sha", byName: false });
      expect([...portLinks(processor, "steps.fix.inputs.base_sha").downstream]).toContain("steps.gate.inputs.base");
      expect([...portLinks(processor, "steps.gate.inputs.base").upstream]).toContain("steps.fix.inputs.base_sha");
    });

    it("a carried input takes the previous try's result field", () => {
      expect(byRef.get("steps.agent.inputs.instruction")).toMatchObject({
        reads: "steps.fix.inputs.instruction",
        byName: true,
        carried: [{ ref: "steps.gate.outputs.instruction", conditional: false, behindWire: false }],
      });
      expect([...portLinks(processor, "steps.gate.outputs.instruction").downstream]).toContain(
        "steps.agent.inputs.instruction",
      );
      expect([...portLinks(processor, "steps.agent.inputs.instruction").upstream]).toContain(
        "steps.gate.outputs.instruction",
      );
    });

    it("index and iteration are the engine's, over a loop input of the same name", () => {
      expect(byRef.get("steps.agent.inputs.index")).toMatchObject({ reads: "loop.index", byName: true });
      expect([...portLinks(processor, "steps.fix.inputs.index").downstream]).not.toContain("steps.agent.inputs.index");
    });

    it("a retry loop's outputs come only from its final body step", () => {
      const up = [...portLinks(processor, "steps.fix.outputs.verdict").upstream];
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

    it("a conditional final step: every step that may end the try is a source, the conditional ones marked", () => {
      const wf: WorkflowDef = {
        id: "c",
        name: "c",
        steps: [
          {
            id: "loop",
            kind: "retry_until",
            max_iterations: 3,
            config: { carry: { note: "note" } },
            inputs: [],
            outputs: [
              { name: "note", type: "string" },
              { name: "iterations", type: "integer" },
            ],
            body: [
              { id: "early", kind: "code", inputs: [{ name: "note", type: "string", required: false }], outputs: [{ name: "note" }] },
              { id: "base", kind: "code", inputs: [], outputs: [{ name: "note" }, { name: "iterations" }] },
              {
                id: "maybe",
                kind: "code",
                config: { when: { field: "x", op: "eq", value: 1 } },
                inputs: [],
                outputs: [{ name: "note" }],
              },
            ],
          },
        ],
        edges: [],
        outputs: [],
      };
      const up = portLinks(wf, "steps.loop.outputs.note").upstream;
      expect(up).toContain("steps.base.outputs.note");
      expect(up).toContain("steps.maybe.outputs.note");
      expect(up).not.toContain("steps.early.outputs.note");
      const note = debugPorts(wf).find((p) => p.ref === "steps.early.inputs.note")!;
      expect(note.carried).toEqual([
        { ref: "steps.base.outputs.note", conditional: false, behindWire: false },
        { ref: "steps.maybe.outputs.note", conditional: true, behindWire: false },
      ]);
      const carry = allLinks(wf).filter((l) => l.kind === "carry");
      expect(carry).toEqual([
        { from: "steps.base.outputs.note", to: "steps.early.inputs.note", kind: "carry", conditional: false },
        { from: "steps.maybe.outputs.note", to: "steps.early.inputs.note", kind: "carry", conditional: true },
      ]);
    });

    it("a retry loop's iterations output is the engine's count, not a body output", () => {
      const wf: WorkflowDef = {
        id: "c",
        name: "c",
        steps: [
          {
            id: "loop",
            kind: "retry_until",
            max_iterations: 3,
            inputs: [],
            outputs: [{ name: "iterations", type: "integer" }],
            body: [{ id: "only", kind: "code", inputs: [], outputs: [{ name: "iterations", type: "integer" }] }],
          },
        ],
        edges: [],
        outputs: [],
      };
      expect([...portLinks(wf, "steps.loop.outputs.iterations").upstream]).not.toContain(
        "steps.only.outputs.iterations",
      );
    });

    it("a wired body input still falls back to its loop's implicit values when the wire supplies nothing", () => {
      const wf: WorkflowDef = {
        id: "f",
        name: "f",
        steps: [
          {
            id: "loop",
            kind: "retry_until",
            max_iterations: 3,
            config: { carry: { note: "note" } },
            inputs: [{ name: "note", type: "string" }],
            outputs: [],
            body: [
              {
                id: "maybe",
                kind: "code",
                config: { when: { field: "x", op: "eq", value: 1 } },
                inputs: [],
                outputs: [{ name: "note", type: "string" }, { name: "index", type: "integer" }],
              },
              {
                id: "use",
                kind: "code",
                inputs: [
                  { name: "note", type: "string" },
                  { name: "index", type: "integer" },
                ],
                outputs: [{ name: "note", type: "string" }],
              },
            ],
          },
        ],
        edges: [
          { source: "maybe", source_port: "note", target: "use", target_port: "note" },
          { source: "maybe", source_port: "index", target: "use", target_port: "index" },
        ],
        outputs: [],
      };
      const byRef = new Map(debugPorts(wf).map((p) => [p.ref, p]));
      expect(byRef.get("steps.use.inputs.note")).toMatchObject({
        reads: "steps.maybe.outputs.note",
        byName: false,
        fallback: "steps.loop.inputs.note",
        // `use` always runs: the only condition is the wire supplying nothing
        carried: [{ ref: "steps.use.outputs.note", conditional: false, behindWire: true }],
      });
      expect(byRef.get("steps.use.inputs.index")).toMatchObject({ reads: "steps.maybe.outputs.index", fallback: "loop.index" });
      expect(allLinks(wf).filter((l) => l.to === "steps.use.inputs.note")).toEqual([
        { from: "steps.maybe.outputs.note", to: "steps.use.inputs.note", kind: "wire" },
        { from: "steps.loop.inputs.note", to: "steps.use.inputs.note", kind: "by-name", fallback: true },
        { from: "steps.use.outputs.note", to: "steps.use.inputs.note", kind: "carry", conditional: false, fallback: true },
      ]);
      expect([...portLinks(wf, "steps.loop.inputs.note").downstream]).toContain("steps.use.inputs.note");
      expect([...portLinks(wf, "steps.use.inputs.note").upstream]).toContain("steps.loop.inputs.note");
    });

    it("an unwired, unfilled input has no fallback", () => {
      expect(debugPorts(SUMMARIZE_DOCUMENT).every((p) => p.fallback === null)).toBe(true);
    });

    it("a loop input wired to a body input of its own name is one link, the wire", () => {
      const wf: WorkflowDef = {
        id: "d",
        name: "d",
        steps: [
          {
            id: "loop",
            kind: "retry_until",
            max_iterations: 2,
            inputs: [{ name: "repo", type: "string" }],
            outputs: [],
            body: [{ id: "one", kind: "code", inputs: [{ name: "repo", type: "string" }], outputs: [] }],
          },
        ],
        edges: [{ source: "loop", source_port: "repo", target: "one", target_port: "repo" }],
        outputs: [],
      };
      expect(allLinks(wf).filter((l) => l.to === "steps.one.inputs.repo")).toEqual([
        { from: "steps.loop.inputs.repo", to: "steps.one.inputs.repo", kind: "wire" },
      ]);
      const ids = allLinks(wf).map((l) => `${l.from}->${l.to}`);
      expect(new Set(ids).size).toBe(ids.length);
    });
  });
});
