/**
 * Pure edits to a workflow's own interface: its inputs, outputs and
 * variables (the canvas's `in` / `out` nodes). Nothing here touches React or
 * mutates its input.
 *
 * Renaming follows references rather than refusing: renaming an input
 * rewrites every wire out of `inputs.<old>` and every output source
 * `inputs.<old>`; renaming a variable rewrites output sources `vars.<old>`.
 * Removing one unwires what read it (the wire goes, an output source is
 * unset), and a type change drops whatever no longer fits — the same
 * pruning a step's port edits get. A name that would be empty, ambiguous
 * (`.` or whitespace break the `inputs.<n>` reference form) or taken is
 * refused before it reaches the draft (`ioNameProblem`).
 */
import type { Port, PortType, WorkflowDef, WorkflowOutput } from "../api/workflows";
import { INPUTS_NODE, parseOutputSource, portsCompatible, prune, sourcePort, sourceVariable, stepLabel } from "./model";

export type Variable = NonNullable<WorkflowDef["variables"]>[number];

/** Guidance codes (api/guidance.ts) a refused name answers. */
export type NameProblem = "empty" | "unsafe_id" | "duplicate";

const NAME = /^[A-Za-z0-9_-]+$/;

/** Why `next` cannot name an input / output / variable among `others`, or null. */
export function ioNameProblem(others: readonly string[], next: string): NameProblem | null {
  if (next === "") return "empty";
  if (!NAME.test(next)) return "unsafe_id";
  if (others.includes(next)) return "duplicate";
  return null;
}

function freeName(taken: readonly string[], prefix: string): string {
  let n = 1;
  while (taken.includes(`${prefix}${n}`)) n += 1;
  return `${prefix}${n}`;
}

const inputsOf = (wf: WorkflowDef) => wf.inputs ?? [];
const outputsOf = (wf: WorkflowDef) => wf.outputs ?? [];
const varsOf = (wf: WorkflowDef) => wf.variables ?? [];

/** Output sources passed through `fn` (null stays null). */
const mapSources = (wf: WorkflowDef, fn: (source: string) => string | null): WorkflowDef => ({
  ...wf,
  outputs: outputsOf(wf).map((o) => (o.source ? { ...o, source: fn(o.source) } : o)),
});

// --------------------------------------------------------------------------- inputs

export function addInput(wf: WorkflowDef): { workflow: WorkflowDef; name: string } {
  const name = freeName(
    inputsOf(wf).map((p) => p.name),
    "input",
  );
  return { workflow: { ...wf, inputs: [...inputsOf(wf), { name, type: "any" }] }, name };
}

export function renameInput(wf: WorkflowDef, from: string, to: string): WorkflowDef {
  const next: WorkflowDef = {
    ...wf,
    inputs: inputsOf(wf).map((p) => (p.name === from ? { ...p, name: to } : p)),
    edges: (wf.edges ?? []).map((e) =>
      e.source === INPUTS_NODE && e.source_port === from ? { ...e, source_port: to } : e,
    ),
  };
  return mapSources(next, (s) => (s === `inputs.${from}` ? `inputs.${to}` : s));
}

export function removeInput(wf: WorkflowDef, name: string): WorkflowDef {
  return prune({ ...wf, inputs: inputsOf(wf).filter((p) => p.name !== name) });
}

/** Type, required, description (never the name: see renameInput). */
export function patchInput(wf: WorkflowDef, name: string, patch: Partial<Omit<Port, "name">>): WorkflowDef {
  return prune({ ...wf, inputs: inputsOf(wf).map((p) => (p.name === name ? { ...p, ...patch } : p)) });
}

// --------------------------------------------------------------------------- outputs

export function addOutput(wf: WorkflowDef): { workflow: WorkflowDef; name: string } {
  const name = freeName(
    outputsOf(wf).map((o) => o.name),
    "output",
  );
  return { workflow: { ...wf, outputs: [...outputsOf(wf), { name, type: "any", source: null }] }, name };
}

/** An output's name is read by nothing inside the workflow; its source travels with it. */
export function renameOutput(wf: WorkflowDef, from: string, to: string): WorkflowDef {
  return { ...wf, outputs: outputsOf(wf).map((o) => (o.name === from ? { ...o, name: to } : o)) };
}

export function removeOutput(wf: WorkflowDef, name: string): WorkflowDef {
  return { ...wf, outputs: outputsOf(wf).filter((o) => o.name !== name) };
}

export function patchOutput(
  wf: WorkflowDef,
  name: string,
  patch: Partial<Omit<WorkflowOutput, "name">>,
): WorkflowDef {
  return prune({ ...wf, outputs: outputsOf(wf).map((o) => (o.name === name ? { ...o, ...patch } : o)) });
}

export interface SourceChoice {
  value: string;
  label: string;
}

/** Everything an output may read whose type fits: inputs, variables, step output ports. */
export function outputSourceOptions(wf: WorkflowDef, output: string): SourceChoice[] {
  const into: PortType | undefined = outputsOf(wf).find((o) => o.name === output)?.type;
  const options: SourceChoice[] = [];
  for (const p of inputsOf(wf)) {
    if (portsCompatible(p.type, into)) options.push({ value: `inputs.${p.name}`, label: `Inputs · ${p.name}` });
  }
  for (const v of varsOf(wf)) {
    if (portsCompatible(v.type, into)) options.push({ value: `vars.${v.name}`, label: `Variables · ${v.name}` });
  }
  for (const s of wf.steps ?? []) {
    for (const p of s.outputs ?? []) {
      if (!portsCompatible(p.type, into)) continue;
      options.push({ value: `steps.${s.id}.outputs.${p.name}`, label: `${stepLabel(s)} · ${p.name}` });
    }
  }
  return options;
}

/** A typed source reference's problem (a guidance code), or null; empty means "not set". */
export function outputSourceProblem(
  wf: WorkflowDef,
  output: string,
  source: string,
): "invalid_reference" | "output_type_mismatch" | null {
  if (source === "") return null;
  const into = outputsOf(wf).find((o) => o.name === output)?.type;
  let type: PortType | undefined;
  if (source.startsWith("vars.")) {
    const v = sourceVariable(wf, source);
    if (!v) return "invalid_reference";
    type = v.type;
  } else {
    const from = parseOutputSource(source);
    const port = from ? sourcePort(wf, from.node, from.port) : undefined;
    if (!port) return "invalid_reference";
    type = port.type;
  }
  return portsCompatible(type, into) ? null : "output_type_mismatch";
}

// --------------------------------------------------------------------------- variables

export function addVariable(wf: WorkflowDef): { workflow: WorkflowDef; name: string } {
  const name = freeName(
    varsOf(wf).map((v) => v.name),
    "var",
  );
  return { workflow: { ...wf, variables: [...varsOf(wf), { name, type: "any" }] }, name };
}

export function renameVariable(wf: WorkflowDef, from: string, to: string): WorkflowDef {
  const next = { ...wf, variables: varsOf(wf).map((v) => (v.name === from ? { ...v, name: to } : v)) };
  return mapSources(next, (s) => (s === `vars.${from}` ? `vars.${to}` : s));
}

export function removeVariable(wf: WorkflowDef, name: string): WorkflowDef {
  return prune({ ...wf, variables: varsOf(wf).filter((v) => v.name !== name) });
}

/** Type or default; `default: undefined` removes the default. */
export function patchVariable(
  wf: WorkflowDef,
  name: string,
  patch: Partial<Omit<Variable, "name">>,
): WorkflowDef {
  const variables = varsOf(wf).map((v) => {
    if (v.name !== name) return v;
    const next: Variable = { ...v, ...patch };
    if ("default" in patch && patch.default === undefined) delete next.default;
    return next;
  });
  return prune({ ...wf, variables });
}
