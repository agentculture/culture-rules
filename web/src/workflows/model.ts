/**
 * The Workflows editor's pure model: a workflow definition
 * (schemas/workflow.schema.json) in, graph facts and edited definitions out.
 * Nothing here touches React or the network, and nothing mutates its input.
 *
 * Machine hops (the board's dashed edges) follow one rule:
 *   - a step runs on its placement's machine, its actor's home machine, or —
 *     unplaced — on the engine node; a capability requirement is unresolved
 *     until the engine places it, so it may land anywhere;
 *   - workflow inputs are pinned into the run when it starts and travel with
 *     every dispatch, so wires out of `inputs` are never hops;
 *   - outputs are collected on the engine node;
 *   - an edge is a hop when its two ends are on different machines, or when
 *     either end is unresolved (it *may* cross).
 * With a run overlaid, the host a step actually ran on replaces its placement.
 */
import type { Machine, Placement } from "../api/types";
import {
  WORKFLOW_FIELDS,
  type Actor,
  type Port,
  type PortType,
  type RunDoc,
  type Step,
  type WorkflowDef,
  type WorkflowEdge,
} from "../api/workflows";

export const INPUTS_NODE = "inputs";
export const OUTPUTS_NODE = "outputs";

export interface PlacementContext {
  machines: readonly Machine[];
  actors: readonly Actor[];
}

export type PlacementMode = "machine" | "actor" | "requirement" | "default";

/** The engine node: the machine holding the `engine_node` role, else the first. */
export function engineMachine(machines: readonly Machine[]): string | null {
  const engine = machines.find((m) => (m.roles ?? []).includes("engine_node"));
  return engine?.name ?? machines[0]?.name ?? null;
}

export function placementMode(p: Placement | null | undefined): PlacementMode {
  if (p?.machine) return "machine";
  if (p?.actor) return "actor";
  if (p?.requirement && p.requirement.length > 0) return "requirement";
  return "default";
}

export function placementLabel(p: Placement | null | undefined): string {
  switch (placementMode(p)) {
    case "machine":
      return `on ${p!.machine}`;
    case "actor":
      return `via ${p!.actor}`;
    case "requirement":
      return `needs ${p!.requirement!.join(", ")}`;
    default:
      return "anywhere";
  }
}

/**
 * An actor's `machine` as the enrolled machine's name (the key the palette
 * and dots use): exact, else case-insensitive, else by host label
 * ("thor.local" -> "thor"); null when it is unset or names no enrolled machine.
 */
function enrolledMachine(raw: string | null | undefined, machines: readonly Machine[]): string | null {
  if (!raw) return null;
  const names = machines.map((m) => m.name);
  if (names.includes(raw)) return raw;
  const lower = raw.toLowerCase();
  const label = lower.split(".")[0];
  return (
    names.find((n) => n.toLowerCase() === lower) ??
    names.find((n) => n.toLowerCase() === label) ??
    null
  );
}

/** Where a step runs, as far as its definition says. */
export function stepMachine(
  step: Step,
  ctx: PlacementContext,
): { machine: string | null; mode: PlacementMode } {
  const mode = placementMode(step.placement);
  if (mode === "machine") return { machine: step.placement!.machine!, mode };
  if (mode === "actor") {
    const ref = step.placement!.actor;
    const actor = ctx.actors.find((a) => a.id === ref) ?? ctx.actors.find((a) => a.name === ref);
    return { machine: enrolledMachine(actor?.machine, ctx.machines), mode };
  }
  if (mode === "requirement") return { machine: null, mode };
  return { machine: engineMachine(ctx.machines), mode };
}

/**
 * The one machine every step of a workflow runs on (by `stepMachine`, as the
 * canvas colours its cards), for the list row's dot; null — neutral — when
 * the steps span machines, any step is unresolved, or there are no steps.
 */
export function workflowMachine(wf: WorkflowDef, ctx: PlacementContext): string | null {
  const hosts = new Set((wf.steps ?? []).map((s) => stepMachine(s, ctx).machine));
  if (hosts.size !== 1) return null;
  const [only] = hosts;
  return only;
}

/** A source port's value may flow into a target port of this type. */
export function portsCompatible(source: PortType = "any", target: PortType = "any"): boolean {
  if (source === "any" || target === "any" || source === target) return true;
  return source === "integer" && target === "number";
}

const steps = (wf: WorkflowDef) => wf.steps ?? [];
const findStep = (wf: WorkflowDef, id: string) => steps(wf).find((s) => s.id === id);

export const stepLabel = (step: Step) => step.name || step.id;

/** The port a wire leaves from: a workflow input, or a step output. */
export function sourcePort(wf: WorkflowDef, node: string, port: string): Port | undefined {
  if (node === INPUTS_NODE) return (wf.inputs ?? []).find((p) => p.name === port);
  return (findStep(wf, node)?.outputs ?? []).find((p) => p.name === port);
}

/** The port a wire arrives at: a step input, or a workflow output. */
export function targetPort(wf: WorkflowDef, node: string, port: string): Port | undefined {
  if (node === OUTPUTS_NODE) {
    const out = (wf.outputs ?? []).find((o) => o.name === port);
    return out ? { name: out.name, type: out.type } : undefined;
  }
  return (findStep(wf, node)?.inputs ?? []).find((p) => p.name === port);
}

export interface StepRun {
  status: string;
  host: string | null;
  attempt: number;
  error: string | null;
}

export type RunOverlay = Map<string, StepRun>;

function errorText(error: unknown): string | null {
  if (error === null || error === undefined) return null;
  if (typeof error === "string") return error;
  if (typeof error === "object" && "message" in error) return String((error as { message: unknown }).message);
  return JSON.stringify(error);
}

/** Each top-level step's persisted state, keyed by step id (loop bodies and the action excluded). */
export function runOverlay(run: RunDoc): RunOverlay {
  const overlay: RunOverlay = new Map();
  for (const st of run.steps ?? []) {
    if (st.loop || st.key.startsWith("@")) continue;
    overlay.set(st.def ?? st.key, {
      status: st.status,
      host: st.host ?? null,
      attempt: st.attempt ?? 0,
      error: errorText(st.error),
    });
  }
  return overlay;
}

export interface GraphEdge {
  id: string;
  source: string;
  sourcePort: string;
  target: string;
  targetPort: string;
  /** Crosses (or may cross) machines: drawn dashed. */
  cross: boolean;
  kind: "wire" | "output";
}

export const edgeId = (source: string, sourcePort: string, target: string, targetPort: string) =>
  `${source}.${sourcePort}->${target}.${targetPort}`;

/** `steps.<id>.outputs.<port>` / `inputs.<n>` -> the node and port it reads. */
export function parseOutputSource(source: string | null | undefined) {
  if (!source) return null;
  const parts = source.split(".");
  if (parts.length === 4 && parts[0] === "steps" && parts[2] === "outputs") {
    return { node: parts[1], port: parts[3] };
  }
  if (parts.length === 2 && parts[0] === "inputs") return { node: INPUTS_NODE, port: parts[1] };
  return null;
}

export function graphEdges(
  wf: WorkflowDef,
  ctx: PlacementContext,
  overlay?: RunOverlay | null,
): GraphEdge[] {
  const engine = engineMachine(ctx.machines);
  const hostOf = (node: string): string | null => {
    if (node === OUTPUTS_NODE) return engine;
    const ran = overlay?.get(node)?.host;
    if (ran) return ran;
    const step = findStep(wf, node);
    return step ? stepMachine(step, ctx).machine : null;
  };
  const cross = (source: string, target: string) => {
    if (source === INPUTS_NODE) return false;
    const s = hostOf(source);
    const t = hostOf(target);
    return s === null || t === null || s !== t;
  };
  const known = (node: string) => node === INPUTS_NODE || findStep(wf, node) !== undefined;

  const wires: GraphEdge[] = (wf.edges ?? [])
    .filter((e) => known(e.source) && findStep(wf, e.target) !== undefined)
    .map((e) => ({
      id: edgeId(e.source, e.source_port, e.target, e.target_port),
      source: e.source,
      sourcePort: e.source_port,
      target: e.target,
      targetPort: e.target_port,
      cross: cross(e.source, e.target),
      kind: "wire" as const,
    }));
  const outputs: GraphEdge[] = [];
  for (const out of wf.outputs ?? []) {
    const from = parseOutputSource(out.source);
    if (!from || !known(from.node)) continue;
    outputs.push({
      id: edgeId(from.node, from.port, OUTPUTS_NODE, out.name),
      source: from.node,
      sourcePort: from.port,
      target: OUTPUTS_NODE,
      targetPort: out.name,
      cross: cross(from.node, OUTPUTS_NODE),
      kind: "output",
    });
  }
  return [...wires, ...outputs];
}

/** The edges a run actually travelled: the source succeeded and the target was reached. */
export function litEdges(edges: readonly GraphEdge[], overlay: RunOverlay | null | undefined): Set<string> {
  const lit = new Set<string>();
  if (!overlay || overlay.size === 0) return lit;
  const ran = (node: string) => node === INPUTS_NODE || overlay.get(node)?.status === "succeeded";
  const reached = (node: string) => {
    if (node === OUTPUTS_NODE) return true;
    const status = overlay.get(node)?.status;
    return status !== undefined && status !== "pending";
  };
  for (const e of edges) if (ran(e.source) && reached(e.target)) lit.add(e.id);
  return lit;
}

// --------------------------------------------------------------------------- Detailed bundles

/**
 * An edge the Detailed view draws: either one port-to-port wire (an end is the
 * expanded node) or a bundle of every wire between one (source, target) pair.
 */
export interface DrawnEdge {
  /** A wire keeps its own id; a bundle's is `bundle:<source>-><target>`, whatever wires it holds. */
  id: string;
  source: string;
  target: string;
  /** The wire's ports; null on a bundle, which joins the cards' bundle handles. */
  sourcePort: string | null;
  targetPort: string | null;
  kind: GraphEdge["kind"];
  /** How many wires it stands for (1 for a port-to-port wire). */
  count: number;
  /** Any of its wires crosses (or may cross) machines: drawn dashed. */
  cross: boolean;
  /** Any of its wires is lit by the run overlay. */
  lit: boolean;
  bundled: boolean;
  /** The wires it stands for, in input order. */
  wires: GraphEdge[];
}

export const bundleId = (source: string, target: string) => `bundle:${source}->${target}`;

/**
 * The Detailed view's drawn edges. Wires touching `expanded` (the selected
 * card, ports showing) stay port to port, each with its own id and fields;
 * every other (source, target) pair collapses into one bundle that counts its
 * wires, is dashed if any wire hops machines and lit if any wire is lit.
 * Every input wire is in exactly one drawn edge's `wires`. Order: each drawn
 * edge where its first wire appears in `edges`.
 */
export function bundleEdges(
  edges: readonly GraphEdge[],
  lit: ReadonlySet<string>,
  expanded: string | null,
): DrawnEdge[] {
  const out: DrawnEdge[] = [];
  const bundles = new Map<string, DrawnEdge>();
  for (const e of edges) {
    const isLit = lit.has(e.id);
    if (expanded !== null && (e.source === expanded || e.target === expanded)) {
      out.push({ ...e, count: 1, lit: isLit, bundled: false, wires: [e] });
      continue;
    }
    const id = bundleId(e.source, e.target);
    const bundle = bundles.get(id);
    if (bundle) {
      bundle.count += 1;
      bundle.cross ||= e.cross;
      bundle.lit ||= isLit;
      bundle.wires.push(e);
      continue;
    }
    const fresh: DrawnEdge = {
      id,
      source: e.source,
      target: e.target,
      sourcePort: null,
      targetPort: null,
      kind: e.kind,
      count: 1,
      cross: e.cross,
      lit: isLit,
      bundled: true,
      wires: [e],
    };
    bundles.set(id, fresh);
    out.push(fresh);
  }
  return out;
}

/**
 * The id of a card's one bundle handle on a side (`in`: the target on its left,
 * `out`: the source on its right): `__in` / `__out`, lengthened with `_` until it
 * names none of the card's ports on either side. React Flow already looks handles up
 * by type, so only same-side names could collide; avoiding both is cheap insurance.
 */
export function bundleHandle(side: "in" | "out", portNames: readonly string[]): string {
  const taken = new Set(portNames);
  let id = `__${side}`;
  while (taken.has(id)) id += "_";
  return id;
}

/** The ports on a node's `in` (target) or `out` (source) side, by name. */
export function nodePortNames(wf: WorkflowDef, node: string, side: "in" | "out"): string[] {
  if (node === INPUTS_NODE) return side === "out" ? (wf.inputs ?? []).map((p) => p.name) : [];
  if (node === OUTPUTS_NODE) return side === "in" ? (wf.outputs ?? []).map((o) => o.name) : [];
  const step = findStep(wf, node);
  return ((side === "in" ? step?.inputs : step?.outputs) ?? []).map((p) => p.name);
}

// --------------------------------------------------------------------------- edits

const withSteps = (wf: WorkflowDef, next: Step[]): WorkflowDef => ({ ...wf, steps: next });

function mapStep(wf: WorkflowDef, id: string, fn: (s: Step) => Step): WorkflowDef {
  return withSteps(
    wf,
    steps(wf).map((s) => (s.id === id ? fn(s) : s)),
  );
}

/** Exactly one of machine | actor | requirement, or null (the engine default). */
export function normalisePlacement(p: Placement | null | undefined): Placement | null {
  switch (placementMode(p)) {
    case "machine":
      return { machine: p!.machine! };
    case "actor":
      return { actor: p!.actor! };
    case "requirement":
      return { requirement: [...p!.requirement!] };
    default:
      return null;
  }
}

export function setPlacement(wf: WorkflowDef, id: string, placement: Placement | null): WorkflowDef {
  return mapStep(wf, id, (s) => ({ ...s, placement: normalisePlacement(placement) }));
}

export function toggleStep(wf: WorkflowDef, id: string): WorkflowDef {
  return mapStep(wf, id, (s) => ({ ...s, enabled: s.enabled === false }));
}

export function deleteStep(wf: WorkflowDef, id: string): WorkflowDef {
  return {
    ...wf,
    steps: steps(wf).filter((s) => s.id !== id),
    edges: (wf.edges ?? []).filter((e) => e.source !== id && e.target !== id),
    outputs: (wf.outputs ?? []).map((o) =>
      parseOutputSource(o.source)?.node === id ? { ...o, source: null } : o,
    ),
  };
}

export function addStep(wf: WorkflowDef): { workflow: WorkflowDef; id: string } {
  const taken = new Set(steps(wf).map((s) => s.id));
  let n = 1;
  while (taken.has(`step-${n}`)) n += 1;
  const id = `step-${n}`;
  const step: Step = {
    id,
    name: "New step",
    kind: "logic",
    inputs: [],
    outputs: [],
    placement: null,
    enabled: true,
  };
  return { workflow: withSteps(wf, [...steps(wf), step]), id };
}

/** The variable an output source `vars.<n>` reads, if it names one. */
export function sourceVariable(wf: WorkflowDef, source: string | null | undefined) {
  const parts = (source ?? "").split(".");
  if (parts.length !== 2 || parts[0] !== "vars") return undefined;
  return (wf.variables ?? []).find((v) => v.name === parts[1]);
}

/** Wires and output sources that still fit their ports' names and types. */
export function prune(wf: WorkflowDef): WorkflowDef {
  const edges = (wf.edges ?? []).filter((e) => {
    const s = sourcePort(wf, e.source, e.source_port);
    const t = targetPort(wf, e.target, e.target_port);
    return s !== undefined && t !== undefined && portsCompatible(s.type, t.type);
  });
  const outputs = (wf.outputs ?? []).map((o) => {
    const from = parseOutputSource(o.source);
    if (!from) {
      if (!o.source?.startsWith("vars.")) return o;
      const v = sourceVariable(wf, o.source);
      return v && portsCompatible(v.type, o.type) ? o : { ...o, source: null };
    }
    const s = sourcePort(wf, from.node, from.port);
    return s && portsCompatible(s.type, o.type) ? o : { ...o, source: null };
  });
  return { ...wf, edges, outputs };
}

/** Patch a step (name, kind, ports, ...); wires that no longer fit are dropped. */
export function updateStep(wf: WorkflowDef, id: string, patch: Partial<Omit<Step, "id">>): WorkflowDef {
  return prune(mapStep(wf, id, (s) => ({ ...s, ...patch })));
}

export interface Connection {
  source: string;
  sourcePort: string;
  target: string;
  targetPort: string;
}

export type ConnectResult = { ok: true; workflow: WorkflowDef } | { ok: false; reason: string };

function reaches(wf: WorkflowDef, from: string, to: string): boolean {
  const seen = new Set<string>();
  const stack = [from];
  while (stack.length) {
    const node = stack.pop()!;
    if (node === to) return true;
    if (seen.has(node)) continue;
    seen.add(node);
    for (const e of wf.edges ?? []) if (e.source === node) stack.push(e.target);
  }
  return false;
}

/** Explain why a connection is refused, or null when it may be wired. */
export function connectionProblem(wf: WorkflowDef, c: Connection): string | null {
  const s = sourcePort(wf, c.source, c.sourcePort);
  const t = targetPort(wf, c.target, c.targetPort);
  if (!s || !t) return "no such port";
  if (c.source === c.target) return "a step cannot feed itself";
  if (!portsCompatible(s.type, t.type)) {
    return `cannot wire ${s.name} (${s.type ?? "any"}) into ${t.name} (${t.type ?? "any"})`;
  }
  if (c.target !== OUTPUTS_NODE && c.source !== INPUTS_NODE && reaches(wf, c.target, c.source)) {
    return "that wire would make a loop";
  }
  return null;
}

/** Wire a source port to a target port, replacing whatever fed that target port. */
export function connect(wf: WorkflowDef, c: Connection): ConnectResult {
  const problem = connectionProblem(wf, c);
  if (problem) return { ok: false, reason: problem };
  if (c.target === OUTPUTS_NODE) {
    const ref = c.source === INPUTS_NODE ? `inputs.${c.sourcePort}` : `steps.${c.source}.outputs.${c.sourcePort}`;
    return {
      ok: true,
      workflow: {
        ...wf,
        outputs: (wf.outputs ?? []).map((o) => (o.name === c.targetPort ? { ...o, source: ref } : o)),
      },
    };
  }
  const kept = (wf.edges ?? []).filter((e) => !(e.target === c.target && e.target_port === c.targetPort));
  const edge: WorkflowEdge = {
    source: c.source,
    source_port: c.sourcePort,
    target: c.target,
    target_port: c.targetPort,
  };
  return { ok: true, workflow: { ...wf, edges: [...kept, edge] } };
}

/** Remove the wire into a step's input port (the "not wired" choice). */
export function disconnect(wf: WorkflowDef, target: string, port: string): WorkflowDef {
  return {
    ...wf,
    edges: (wf.edges ?? []).filter((e) => !(e.target === target && e.target_port === port)),
  };
}

export interface SourceOption {
  source: string;
  port: string;
  label: string;
}

/** The upstream ports whose type fits a step's input port (never the step itself). */
export function compatibleSources(wf: WorkflowDef, stepId: string, portName: string): SourceOption[] {
  const into = targetPort(wf, stepId, portName);
  if (!into) return [];
  const options: SourceOption[] = [];
  for (const p of wf.inputs ?? []) {
    if (portsCompatible(p.type, into.type)) {
      options.push({ source: INPUTS_NODE, port: p.name, label: `Inputs · ${p.name}` });
    }
  }
  for (const s of steps(wf)) {
    if (s.id === stepId) continue;
    for (const p of s.outputs ?? []) {
      if (!portsCompatible(p.type, into.type)) continue;
      if (connectionProblem(wf, { source: s.id, sourcePort: p.name, target: stepId, targetPort: portName })) {
        continue;
      }
      options.push({ source: s.id, port: p.name, label: `${stepLabel(s)} · ${p.name}` });
    }
  }
  return options;
}

/** A stored document reduced to workflow.schema.json fields — the PUT body. */
export function toDefinition(doc: WorkflowDef): WorkflowDef {
  const out: Record<string, unknown> = {};
  const src = doc as unknown as Record<string, unknown>;
  for (const key of WORKFLOW_FIELDS) if (key in src && src[key] !== undefined) out[key] = src[key];
  return out as unknown as WorkflowDef;
}
