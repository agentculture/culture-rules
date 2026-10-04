import { describe, expect, it } from "vitest";
import { MACHINES } from "../fixtures/rules-fixture";
import { ACTORS, BUILD_IMAGE, REVIEW_PR, RUN_7, WORKFLOW_DOCS } from "./fixture";
import {
  INPUTS_NODE,
  OUTPUTS_NODE,
  addStep,
  compatibleSources,
  connect,
  deleteStep,
  engineMachine,
  graphEdges,
  litEdges,
  placementLabel,
  portsCompatible,
  runOverlay,
  setPlacement,
  stepMachine,
  toDefinition,
  toggleStep,
  updateStep,
  workflowMachine,
} from "./model";

const ctx = { machines: MACHINES, actors: ACTORS };
const edgeKey = (e: { source: string; sourcePort: string; target: string; targetPort: string }) =>
  `${e.source}.${e.sourcePort}->${e.target}.${e.targetPort}`;

describe("placement", () => {
  it("the engine machine is the one with the engine_node role", () => {
    expect(engineMachine(MACHINES)).toBe("spark");
    expect(engineMachine([{ name: "a" }, { name: "b" }])).toBe("a");
    expect(engineMachine([])).toBeNull();
  });

  it("labels each placement mode in words", () => {
    expect(placementLabel({ machine: "thor" })).toBe("on thor");
    expect(placementLabel({ actor: "claude-reviewer" })).toBe("via claude-reviewer");
    expect(placementLabel({ requirement: ["gpu", "cuda"] })).toBe("needs gpu, cuda");
    expect(placementLabel(null)).toBe("anywhere");
  });

  it("resolves a step's machine through a machine, an actor's home, or not at all", () => {
    const step = (placement: unknown) => ({ id: "s", kind: "code" as const, placement: placement as never });
    expect(stepMachine(step({ machine: "thor" }), ctx)).toEqual({ machine: "thor", mode: "machine" });
    expect(stepMachine(step({ actor: "claude-reviewer" }), ctx)).toEqual({
      machine: "thor",
      mode: "actor",
    });
    expect(stepMachine(step({ actor: "ori" }), ctx)).toEqual({ machine: null, mode: "actor" });
    expect(stepMachine(step({ requirement: ["gpu"] }), ctx)).toEqual({
      machine: null,
      mode: "requirement",
    });
    // Unplaced runs where the engine runs.
    expect(stepMachine(step(null), ctx)).toEqual({ machine: "spark", mode: "default" });
  });

  it("setPlacement keeps exactly one of machine | actor | requirement", () => {
    const wf = setPlacement(REVIEW_PR, "review", { actor: "claude-reviewer" });
    const review = wf.steps!.find((s) => s.id === "review")!;
    expect(review.placement).toEqual({ actor: "claude-reviewer" });
    const req = setPlacement(wf, "review", { requirement: ["gpu"] });
    expect(req.steps!.find((s) => s.id === "review")!.placement).toEqual({ requirement: ["gpu"] });
    const none = setPlacement(wf, "review", null);
    expect(none.steps!.find((s) => s.id === "review")!.placement).toBeNull();
    // Never mutates its input.
    expect(REVIEW_PR.steps!.find((s) => s.id === "review")!.placement).toEqual({ machine: "thor" });
  });
});

describe("typed ports", () => {
  it("connects equal types, and anything to or from `any`", () => {
    expect(portsCompatible("string", "string")).toBe(true);
    expect(portsCompatible("string", "boolean")).toBe(false);
    expect(portsCompatible("any", "boolean")).toBe(true);
    expect(portsCompatible("array", undefined)).toBe(true);
    expect(portsCompatible("integer", "number")).toBe(true);
    expect(portsCompatible("number", "integer")).toBe(false);
  });

  it("refuses a connection whose port types differ", () => {
    const res = connect(REVIEW_PR, {
      source: "run-tests",
      sourcePort: "passed",
      target: "review",
      targetPort: "diff",
    });
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.reason).toMatch(/boolean.*string/);
  });

  it("wires a compatible connection, replacing whatever fed that input", () => {
    const res = connect(REVIEW_PR, {
      source: INPUTS_NODE,
      sourcePort: "repo",
      target: "review",
      targetPort: "diff",
    });
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    const into = res.workflow.edges!.filter((e) => e.target === "review" && e.target_port === "diff");
    expect(into).toEqual([
      { source: "inputs", source_port: "repo", target: "review", target_port: "diff" },
    ]);
  });

  it("wiring an output port to the outputs node sets that output's source", () => {
    const res = connect(REVIEW_PR, {
      source: "fetch-diff",
      sourcePort: "diff",
      target: OUTPUTS_NODE,
      targetPort: "owner",
    });
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.workflow.outputs!.find((o) => o.name === "owner")!.source).toBe(
      "steps.fetch-diff.outputs.diff",
    );
  });

  it("offers only type-compatible upstream ports for an input", () => {
    const options = compatibleSources(REVIEW_PR, "decide", "passed").map((o) => `${o.source}.${o.port}`);
    expect(options).toContain("run-tests.passed");
    expect(options).not.toContain("fetch-diff.diff");
    expect(options).not.toContain("decide.verdict"); // never itself
  });
});

describe("graph edges and machine hops", () => {
  it("draws every wire plus the exported outputs", () => {
    const edges = graphEdges(REVIEW_PR, ctx);
    expect(edges).toHaveLength(8);
    expect(edges.map(edgeKey)).toEqual(
      expect.arrayContaining([
        "inputs.pr->fetch-diff.pr",
        "review.owner->outputs.owner",
        "decide.verdict->outputs.verdict",
      ]),
    );
  });

  it("marks cross-machine hops exactly as the board dashes them", () => {
    const cross = Object.fromEntries(graphEdges(REVIEW_PR, ctx).map((e) => [edgeKey(e), e.cross]));
    expect(cross).toEqual({
      "inputs.pr->fetch-diff.pr": false,
      "inputs.repo->fetch-diff.repo": false,
      "inputs.repo->run-tests.repo": false,
      "fetch-diff.diff->review.diff": true,
      "review.findings->decide.findings": true,
      "run-tests.passed->decide.passed": true,
      "decide.verdict->outputs.verdict": false,
      "review.owner->outputs.owner": true,
    });
  });

  it("a requirement placement may land anywhere, so its hops are dashed", () => {
    const edges = graphEdges(BUILD_IMAGE, ctx);
    expect(edges.find((e) => e.target === OUTPUTS_NODE)!.cross).toBe(true);
  });

  it("with a run, the hosts that actually ran decide the dashes", () => {
    const overlay = runOverlay(RUN_7);
    const moved = setPlacement(REVIEW_PR, "review", { requirement: ["gpu"] });
    const edges = graphEdges(moved, ctx, overlay);
    expect(edges.find((e) => e.target === "review")!.cross).toBe(true); // ran on thor, spark fed it
  });
});

describe("step edits", () => {
  it("toggles a step's enabled flag", () => {
    const off = toggleStep(REVIEW_PR, "review");
    expect(off.steps!.find((s) => s.id === "review")!.enabled).toBe(false);
    expect(toggleStep(off, "review").steps!.find((s) => s.id === "review")!.enabled).toBe(true);
  });

  it("deleting a step drops its wires and unhooks outputs it fed", () => {
    const wf = deleteStep(REVIEW_PR, "review");
    expect(wf.steps!.map((s) => s.id)).toEqual(["fetch-diff", "run-tests", "decide"]);
    expect(wf.edges!.some((e) => e.source === "review" || e.target === "review")).toBe(false);
    expect(wf.outputs!.find((o) => o.name === "owner")!.source).toBeNull();
  });

  it("adds a uniquely named step", () => {
    const first = addStep(REVIEW_PR);
    expect(first.id).toBe("step-1");
    expect(first.workflow.steps!.at(-1)).toMatchObject({ id: "step-1", kind: "logic", enabled: true });
    expect(addStep(first.workflow).id).toBe("step-2");
  });

  it("renaming or retyping a port drops wires that no longer fit", () => {
    const renamed = updateStep(REVIEW_PR, "review", {
      inputs: [{ name: "patch", type: "string" }],
    });
    expect(renamed.edges!.some((e) => e.target === "review")).toBe(false);
    const retyped = updateStep(REVIEW_PR, "decide", {
      inputs: [
        { name: "findings", type: "array" },
        { name: "passed", type: "string" },
      ],
    });
    expect(retyped.edges!.some((e) => e.target === "decide" && e.target_port === "passed")).toBe(false);
    expect(retyped.edges!.some((e) => e.target === "decide" && e.target_port === "findings")).toBe(true);
  });

  it("a PUT body carries only workflow.schema.json fields", () => {
    const def = toDefinition(WORKFLOW_DOCS[0]);
    expect(Object.keys(def)).not.toContain("deleted_at");
    expect(def.steps).toHaveLength(4);
  });
});

describe("run overlay", () => {
  it("maps each step to its host and outcome from persisted run state", () => {
    const overlay = runOverlay(RUN_7);
    expect(overlay.get("review")).toMatchObject({ status: "succeeded", host: "thor" });
    expect(overlay.get("decide")).toMatchObject({ status: "failed", host: "spark", attempt: 2 });
    expect(overlay.has("@action")).toBe(false);
  });

  it("lights the path the run took", () => {
    const overlay = runOverlay(RUN_7);
    const edges = graphEdges(REVIEW_PR, ctx, overlay);
    const lit = litEdges(edges, overlay);
    const litKeys = edges.filter((e) => lit.has(e.id)).map(edgeKey);
    expect(litKeys).toContain("inputs.pr->fetch-diff.pr");
    expect(litKeys).toContain("review.findings->decide.findings");
    expect(litKeys).toContain("review.owner->outputs.owner");
    // Decide failed: its output never flowed.
    expect(litKeys).not.toContain("decide.verdict->outputs.verdict");
  });
});

describe("workflowMachine (the list row's dot)", () => {
  const wf = (steps: { id: string; placement?: Record<string, unknown> }[]) => ({
    id: "w",
    name: "W",
    steps: steps.map((s) => ({ kind: "logic" as const, ...s })),
  });

  it("is the one machine every step runs on", () => {
    expect(workflowMachine(wf([{ id: "a", placement: { machine: "thor" } }]), ctx)).toBe("thor");
    // An actor's home machine and an unplaced step (the engine node) count as the canvas counts them.
    expect(
      workflowMachine(wf([{ id: "a", placement: { actor: "claude-reviewer" } }, { id: "b", placement: { machine: "thor" } }]), ctx),
    ).toBe("thor");
    expect(workflowMachine(wf([{ id: "a" }]), ctx)).toBe("spark");
  });

  it("is neutral (null) when steps are mixed, unresolved, or there are none", () => {
    expect(workflowMachine(REVIEW_PR, ctx)).toBeNull();
    expect(workflowMachine(BUILD_IMAGE, ctx)).toBeNull();
    expect(workflowMachine(wf([]), ctx)).toBeNull();
    expect(workflowMachine({ id: "w", name: "W" }, ctx)).toBeNull();
  });
});

describe("workflowMachine: actor placement resolves to a known machine (#7)", () => {
  const onActor = (actor: string) => ({ id: "s", kind: "code" as const, placement: { actor } });
  const wf = (...steps: ReturnType<typeof onActor>[]) => ({ id: "w", name: "W", steps });
  const thorServer = { id: "thor-server", name: "thor-server", kind: "runner" as const, machine: "thor" };
  const c = { machines: MACHINES, actors: [...ACTORS, thorServer] };

  it("steps placed on thor-server show thor", () => {
    expect(workflowMachine(wf(onActor("thor-server"), onActor("thor-server")), c)).toBe("thor");
  });

  it("steps spanning two hosts stay neutral", () => {
    const sparkRunner = { id: "spark-runner", name: "spark-runner", kind: "runner" as const, machine: "spark" };
    const both = { machines: MACHINES, actors: [thorServer, sparkRunner] };
    expect(workflowMachine(wf(onActor("thor-server"), onActor("spark-runner")), both)).toBeNull();
  });

  it("an actor's machine spelled differently (case, FQDN) maps to the enrolled machine's name", () => {
    // The dot looks its colour up by the enrolled machine's name, so a raw "Thor.local" would go grey.
    for (const machine of ["Thor", "THOR", "thor.local", "thor.tail1234.ts.net"]) {
      expect(workflowMachine(wf(onActor("t")), { machines: MACHINES, actors: [{ ...thorServer, id: "t", machine }] })).toBe("thor");
    }
  });

  it("an actor is found by name when the placement holds its name", () => {
    const a = { ...thorServer, id: "a1b2" };
    expect(workflowMachine(wf(onActor("thor-server")), { machines: MACHINES, actors: [a] })).toBe("thor");
  });

  it("an actor on a machine nobody enrolled stays unresolved", () => {
    const ghost = { ...thorServer, id: "g", machine: "mars" };
    expect(workflowMachine(wf(onActor("g")), { machines: MACHINES, actors: [ghost] })).toBeNull();
  });
});
