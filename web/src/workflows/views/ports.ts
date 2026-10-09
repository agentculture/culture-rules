/**
 * The Debug view's pure model (canvas WF-Variables, WF-Variables-Port): every
 * input and output port of a workflow — its own inputs and outputs, each
 * step's, and each loop body step's — with its type and the reference it
 * reads, plus the data links between ports, so a selected port can light its
 * upstream and downstream.
 *
 * A port is named by the reference a definition would use for it:
 * `inputs.<n>`, `outputs.<n>`, `steps.<id>.inputs.<p>`, `steps.<id>.outputs.<p>`.
 *
 * Links follow the engine (culture_rules/engine/runs.py):
 *   - a wire carries a source port into a step input (`edges`);
 *   - a workflow output reads `inputs.<n>` or `steps.<id>.outputs.<p>`
 *     (a `vars.<n>` source is shown as its reference, not a port);
 *   - a step's outputs come from its inputs (`step`);
 *   - inside a loop, an edge from the loop itself reads the loop's *inputs*
 *     (`_edge_source`);
 *   - a loop body step's unwired input is filled implicitly (`by-name`,
 *     `_implicit_loop_inputs`): `iteration` / `index` (and `item` for
 *     for_each) are the engine's own values, over a loop input of the same
 *     name; otherwise a loop input of the same name;
 *   - a retry_until loop's `config.carry` (`{input: result field}`) feeds the
 *     previous try's result field into that body input from the second try
 *     on (`carry`);
 *   - a loop's result is the outputs of its last body step that *succeeded*
 *     (`_progress_loop`); a step whose `config.when` is false is skipped, so
 *     the possible sources are the last unconditional body step and every
 *     later conditional one (marked `conditional`). A retry_until loop's
 *     outputs are that result's fields of the same name, except `iterations`,
 *     the engine's own count; a for_each loop's are per-name lists, and its
 *     `results` the whole result objects, so every output of a source feeds
 *     it (`_loop_outputs`).
 * Nothing here touches React, and nothing mutates its input.
 */
import type { Port, PortType, Step, WorkflowDef } from "../../api/workflows";
import { INPUTS_NODE, OUTPUTS_NODE, parseOutputSource, stepLabel } from "../model";

export type PortSide = "in" | "out";

export interface DebugPort {
  /** The port's reference, e.g. `steps.review.inputs.diff`; unique in a workflow. */
  ref: string;
  /** `inputs`, `outputs`, or the step id. */
  node: string;
  /** `in` reads a value (step input, workflow output); `out` offers one (workflow input, step output). */
  side: PortSide;
  name: string;
  type: PortType;
  required: boolean;
  /** What an `in` port reads (a port reference, or `vars.<n>`); null when nothing feeds it. */
  reads: string | null;
  /** The input is filled implicitly by its loop (a loop input of its name, or the engine's `loop.<n>`). */
  byName: boolean;
  /**
   * From a retry_until loop's second try on, the previous result field this input takes
   * (`config.carry`): one entry per body step that may produce the result.
   */
  carried: Carried[];
  /** An `out` port that a workflow output reads. */
  exported: boolean;
}

export interface DebugGroup {
  id: string;
  label: string;
  kind: "inputs" | "outputs" | "step";
  step: Step | null;
  inputs: DebugPort[];
  outputs: DebugPort[];
  /** A loop's body steps, drawn inside its card. */
  body: DebugGroup[];
}

export type LinkKind = "wire" | "output" | "step" | "by-name" | "carry";

export interface Carried {
  ref: string;
  /** The source step has a `config.when`: it feeds the value only when it runs. */
  conditional: boolean;
}

export interface PortLink {
  from: string;
  to: string;
  kind: LinkKind;
  /** A loop-result link (`carry`, or into a loop output) whose source step may be skipped. */
  conditional?: boolean;
}

/** A port's reference, as a definition names it. */
export function portRef(node: string, side: PortSide, name: string): string {
  if (node === INPUTS_NODE) return `inputs.${name}`;
  if (node === OUTPUTS_NODE) return `outputs.${name}`;
  return `steps.${node}.${side === "in" ? "inputs" : "outputs"}.${name}`;
}

/** The reference an output's `source` names, normalised to a port reference where it is one. */
function sourceRef(source: string | null | undefined): string | null {
  if (!source) return null;
  const from = parseOutputSource(source);
  return from ? portRef(from.node, "out", from.port) : source;
}

/** Input ports a loop fills with its own values, over a loop input of the same name. */
const ENGINE_INPUTS: Record<string, readonly string[]> = {
  for_each: ["iteration", "index", "item"],
  retry_until: ["iteration", "index"],
};

const isConditional = (step: Step) => step.config != null && "when" in step.config;

/**
 * The body steps whose outputs may be an iteration's result (the last one that
 * succeeded): the last enabled step without a `when`, then every later enabled step
 * with one. With no unconditional step, every enabled step.
 */
function resultSteps(loop: Step): { step: Step; conditional: boolean }[] {
  const enabled = (loop.body ?? []).filter((b) => b.enabled !== false);
  let from = 0;
  enabled.forEach((b, i) => {
    if (!isConditional(b)) from = i;
  });
  return enabled.slice(from).map((step) => ({ step, conditional: isConditional(step) }));
}

/** A retry_until loop's `config.carry`, as `{input name: result field}` string pairs. */
function carryOf(loop: Step | null): Map<string, string> {
  const carry = loop?.kind === "retry_until" ? loop.config?.carry : null;
  const out = new Map<string, string>();
  if (!carry || typeof carry !== "object" || Array.isArray(carry)) return out;
  for (const [name, field] of Object.entries(carry)) if (typeof field === "string") out.set(name, field);
  return out;
}

const topSteps = (wf: WorkflowDef) => wf.steps ?? [];

/** Every step with the loop it sits in (null at top level). */
function allSteps(wf: WorkflowDef): { step: Step; loop: Step | null }[] {
  const out: { step: Step; loop: Step | null }[] = [];
  for (const step of topSteps(wf)) {
    out.push({ step, loop: null });
    for (const inner of step.body ?? []) out.push({ step: inner, loop: step });
  }
  return out;
}

function exportedRefs(wf: WorkflowDef): Set<string> {
  const refs = new Set<string>();
  for (const o of wf.outputs ?? []) {
    const ref = sourceRef(o.source);
    if (ref) refs.add(ref);
  }
  return refs;
}

function toPort(
  node: string,
  side: PortSide,
  p: Pick<Port, "name" | "type" | "required">,
  extra: Partial<Pick<DebugPort, "reads" | "byName" | "carried" | "exported">> = {},
): DebugPort {
  return {
    ref: portRef(node, side, p.name),
    node,
    side,
    name: p.name,
    type: p.type ?? "any",
    required: p.required !== false,
    reads: extra.reads ?? null,
    byName: extra.byName ?? false,
    carried: extra.carried ?? [],
    exported: extra.exported ?? false,
  };
}

function stepGroup(wf: WorkflowDef, step: Step, loop: Step | null, exported: Set<string>): DebugGroup {
  const wired = new Map<string, string>();
  for (const e of wf.edges ?? []) {
    if (e.target !== step.id) continue;
    // inside a loop, an edge from the loop itself reads the loop's inputs (`_edge_source`)
    const side = loop && e.source === loop.id ? "in" : "out";
    wired.set(e.target_port, portRef(e.source, side, e.source_port));
  }
  const loopInputs = new Set((loop?.inputs ?? []).map((p) => p.name));
  const engine = new Set(loop ? (ENGINE_INPUTS[loop.kind] ?? []) : []);
  const carry = carryOf(loop);
  const sources = loop ? resultSteps(loop) : [];
  const inputs = (step.inputs ?? []).map((p) => {
    const reads = wired.get(p.name);
    if (reads) return toPort(step.id, "in", p, { reads });
    const field = carry.get(p.name);
    const carried: Carried[] = field
      ? sources
          .filter(({ step: s }) => (s.outputs ?? []).some((o) => o.name === field))
          .map(({ step: s, conditional }) => ({ ref: portRef(s.id, "out", field), conditional }))
      : [];
    if (engine.has(p.name)) return toPort(step.id, "in", p, { reads: `loop.${p.name}`, byName: true, carried });
    if (loop && loopInputs.has(p.name)) {
      return toPort(step.id, "in", p, { reads: portRef(loop.id, "in", p.name), byName: true, carried });
    }
    return toPort(step.id, "in", p, { carried });
  });
  const outputs = (step.outputs ?? []).map((p) =>
    toPort(step.id, "out", p, { exported: exported.has(portRef(step.id, "out", p.name)) }),
  );
  return {
    id: step.id,
    label: stepLabel(step),
    kind: "step",
    step,
    inputs,
    outputs,
    body: loop ? [] : (step.body ?? []).map((inner) => stepGroup(wf, inner, step, exported)),
  };
}

function ioGroups(wf: WorkflowDef, exported: Set<string>): { inputs: DebugGroup; outputs: DebugGroup } {
  return {
    inputs: {
      id: INPUTS_NODE,
      label: "in",
      kind: "inputs",
      step: null,
      inputs: [],
      outputs: (wf.inputs ?? []).map((p) =>
        toPort(INPUTS_NODE, "out", p, { exported: exported.has(portRef(INPUTS_NODE, "out", p.name)) }),
      ),
      body: [],
    },
    outputs: {
      id: OUTPUTS_NODE,
      label: "out",
      kind: "outputs",
      step: null,
      inputs: (wf.outputs ?? []).map((o) =>
        toPort(OUTPUTS_NODE, "in", { name: o.name, type: o.type }, { reads: sourceRef(o.source) }),
      ),
      outputs: [],
      body: [],
    },
  };
}

/**
 * The cards in columns: `in` first, each top-level step one column after the
 * deepest step it reads (a body step's wires count for its loop), `out` last.
 * Declared order within a column.
 */
export function debugColumns(wf: WorkflowDef): DebugGroup[][] {
  const exported = exportedRefs(wf);
  const io = ioGroups(wf, exported);
  const top = topSteps(wf);
  const owner = new Map<string, string>();
  for (const { step, loop } of allSteps(wf)) owner.set(step.id, loop?.id ?? step.id);

  const feeds = new Map<string, Set<string>>();
  for (const e of wf.edges ?? []) {
    const target = owner.get(e.target);
    const source = owner.get(e.source);
    if (!target || !source || source === target) continue;
    if (!feeds.has(target)) feeds.set(target, new Set());
    feeds.get(target)!.add(source);
  }
  const depth = new Map<string, number>();
  const visiting = new Set<string>();
  const depthOf = (id: string): number => {
    const known = depth.get(id);
    if (known !== undefined) return known;
    if (visiting.has(id)) return 1; // a loop in the wiring: stop here rather than recurse forever
    visiting.add(id);
    let d = 1;
    for (const source of feeds.get(id) ?? []) d = Math.max(d, depthOf(source) + 1);
    visiting.delete(id);
    depth.set(id, d);
    return d;
  };

  const columns: DebugGroup[][] = [[io.inputs]];
  for (const step of top) {
    const d = depthOf(step.id);
    while (columns.length <= d) columns.push([]);
    columns[d].push(stepGroup(wf, step, null, exported));
  }
  columns.push([io.outputs]);
  return columns.filter((c) => c.length > 0);
}

function flatten(groups: readonly DebugGroup[]): DebugGroup[] {
  return groups.flatMap((g) => [g, ...flatten(g.body)]);
}

/** Every port of the workflow, in column order. */
export function debugPorts(wf: WorkflowDef): DebugPort[] {
  return flatten(debugColumns(wf).flat()).flatMap((g) => [...g.inputs, ...g.outputs]);
}

/** Every data link between two ports (see the module comment). */
export function allLinks(wf: WorkflowDef): PortLink[] {
  const groups = flatten(debugColumns(wf).flat());
  const known = new Set(groups.flatMap((g) => [...g.inputs, ...g.outputs]).map((p) => p.ref));
  const links: PortLink[] = [];
  for (const g of groups) {
    for (const p of g.inputs) {
      if (!p.reads || !known.has(p.reads)) continue;
      let kind: LinkKind = "wire";
      if (g.kind === "outputs") kind = "output";
      else if (p.byName) kind = "by-name";
      links.push({ from: p.reads, to: p.ref, kind });
    }
    for (const p of g.inputs) {
      for (const c of p.carried) {
        if (known.has(c.ref)) links.push({ from: c.ref, to: p.ref, kind: "carry", conditional: c.conditional });
      }
    }
    if (g.kind !== "step") continue;
    for (const i of g.inputs) for (const o of g.outputs) links.push({ from: i.ref, to: o.ref, kind: "step" });
    // a loop's result is its last succeeded body step's outputs (`_progress_loop`, `_loop_outputs`)
    if (!g.step || g.body.length === 0) continue;
    const loopKind = g.step.kind;
    for (const { step: src, conditional } of resultSteps(g.step)) {
      const inner = g.body.find((b) => b.id === src.id);
      for (const out of inner ? g.outputs : []) {
        if (loopKind === "retry_until" && out.name === "iterations") continue; // the engine's count
        const gathersAll = loopKind === "for_each" && out.name === "results";
        for (const o of inner!.outputs) {
          if (gathersAll || o.name === out.name) {
            links.push({ from: o.ref, to: out.ref, kind: "by-name", conditional });
          }
        }
      }
    }
  }
  return links;
}

export interface PortRelations {
  upstream: Set<string>;
  downstream: Set<string>;
}

/**
 * The ports a selected port reads from and feeds into. One hop, as the board
 * draws it: upstream is what feeds the port (for a step output, its step's
 * inputs and what feeds them); downstream is what reads it (for a step input,
 * its step's outputs and what reads them). `everything` follows downstream to
 * the end ("Show everything downstream").
 */
export function portLinks(
  wf: WorkflowDef,
  ref: string,
  options: { everything?: boolean } = {},
): PortRelations {
  const links = allLinks(wf);
  const into = (to: string, kinds: (k: LinkKind) => boolean) =>
    links.filter((l) => l.to === to && kinds(l.kind)).map((l) => l.from);
  const outOf = (from: string, kinds: (k: LinkKind) => boolean) =>
    links.filter((l) => l.from === from && kinds(l.kind)).map((l) => l.to);
  const across = (k: LinkKind) => k !== "step";
  const within = (k: LinkKind) => k === "step";

  const upstream = new Set<string>();
  for (const s of into(ref, across)) upstream.add(s);
  for (const i of into(ref, within)) {
    upstream.add(i);
    for (const s of into(i, across)) upstream.add(s);
  }

  const downstream = new Set<string>();
  if (options.everything) {
    const stack = outOf(ref, () => true);
    while (stack.length) {
      const next = stack.pop()!;
      if (next === ref || downstream.has(next)) continue;
      downstream.add(next);
      stack.push(...outOf(next, () => true));
    }
  } else {
    for (const t of outOf(ref, across)) downstream.add(t);
    for (const o of outOf(ref, within)) {
      downstream.add(o);
      for (const t of outOf(o, across)) downstream.add(t);
    }
  }
  upstream.delete(ref);
  return { upstream, downstream };
}

/** Required step inputs that nothing feeds: refused when a run starts. */
export function unboundRequired(wf: WorkflowDef): string[] {
  return debugPorts(wf)
    .filter((p) => p.side === "in" && p.node !== OUTPUTS_NODE && p.required && p.reads === null)
    .map((p) => p.ref);
}
