/**
 * Read-only presentation of stored rules and workflows for all folded views.
 * API definition bodies are open objects (api/openapi.json); retain the original
 * documents, including fields not yet named by the web client's types.
 * No React, network calls, inferred engine defaults, or stored chain metadata.
 */
import type { Condition, Operand, Rule, Workflow } from "../api/types";

export type SharedValue =
  | { kind: "shared"; value: unknown }
  | { kind: "differs"; values: { ruleId: string; value: unknown }[] };

export interface EntryPoint {
  rule: Rule;
  workflowId: string;
  enabled: boolean;
  kind: "entry";
}

export type Predecessor =
  | { kind: "any" }
  | { kind: "linked"; workflowId: string }
  | { kind: "ambiguous"; workflowIds: string[] };

/** A continuation belongs to the workflow it starts, including an unscoped one. */
export interface Continuation {
  rule: Rule;
  workflowId: string;
  enabled: boolean;
  kind: "continuation";
  predecessor: Predecessor;
  /** Null for unscoped or ambiguous predecessors; consult predecessor.kind. */
  fromWorkflowId: string | null;
  fromLabel: string;
}

export type FoldEntry = EntryPoint | Continuation;

export interface FoldWorkflow {
  id: string;
  /** Null preserves reachability when a rule references a missing workflow. */
  workflow: Workflow | null;
  /** All rules owned here, including continuations and disabled rules. */
  entries: FoldEntry[];
  shared: Record<string, SharedValue>;
}

export interface Chain {
  /** Workflow ids in input order; singleton workflows are components too. */
  workflowIds: string[];
  entryPoints: EntryPoint[];
  continuations: Continuation[];
}

export interface FoldModel {
  workflows: FoldWorkflow[];
  /** External starts only; workflow.entries also includes continuations. */
  entryPoints: EntryPoint[];
  continuations: Continuation[];
  chains: Chain[];
  /** No wrapper is fabricated: the editor must offer the D7 writes separately. */
  d7Candidates: Rule[];
}

function workflowLiteral(field: Operand, literal: Operand): string | null {
  return "field" in field && field.field === "data.workflow_id"
    && "literal" in literal && typeof literal.literal === "string"
    ? literal.literal : null;
}

type PredecessorTerm = Extract<Condition, { op: "compare" }>;

/**
 * Return qualifying equality nodes in traversal order, preserving duplicates.
 * Only descend through AND: OR/NOT do not require a predecessor. Exactly one
 * term is editable as a predecessor; string literals include the empty string.
 */
export function predecessorTerms(condition: Condition | null | undefined): PredecessorTerm[] {
  if (!condition) return [];
  if (condition.op === "and") return condition.args.flatMap(predecessorTerms);
  if (condition.op !== "compare" || condition.cmp !== "==") return [];
  const id = workflowLiteral(condition.left, condition.right)
    ?? workflowLiteral(condition.right, condition.left);
  return id === null ? [] : [condition];
}

function entryFor(rule: Rule, workflowId: string): FoldEntry {
  const base = { rule, workflowId, enabled: rule.enabled !== false };
  const params = rule.trigger.params;
  const eventType = params && "type" in params ? params.type : undefined;
  if (rule.trigger.kind !== "event" || typeof eventType !== "string" || !eventType.startsWith("rules.run.")) {
    return { ...base, kind: "entry" };
  }
  const sources = predecessorTerms(rule.condition).map((term) =>
    (workflowLiteral(term.left, term.right) ?? workflowLiteral(term.right, term.left))!);
  const predecessor: Predecessor = sources.length === 0 ? { kind: "any" }
    : sources.length === 1 ? { kind: "linked", workflowId: sources[0] }
      : { kind: "ambiguous", workflowIds: sources };
  const fromWorkflowId = predecessor.kind === "linked" ? predecessor.workflowId : null;
  return {
    ...base, kind: "continuation", predecessor, fromWorkflowId,
    fromLabel: predecessor.kind === "ambiguous" ? "from multiple workflow terms"
      : predecessor.kind === "any" ? "from any workflow" : `from ${predecessor.workflowId}`,
  };
}

/** JSON structural equality: object key order is irrelevant, array order is not. */
function identical(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a === null || b === null || typeof a !== "object" || typeof b !== "object") return false;
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length
      && a.every((value, index) => identical(value, b[index]));
  }
  const left = a as Record<string, unknown>;
  const right = b as Record<string, unknown>;
  const keys = Object.keys(left);
  return keys.length === Object.keys(right).length
    && keys.every((key) => Object.hasOwn(right, key) && identical(left[key], right[key]));
}

/** Entry identity/lifecycle and server-managed metadata must never fan out. */
export const EXCLUDED_RULE_FIELDS = [
  "id", "name", "description", "enabled", "schema_version", "updated_at",
  "deleted_at", "deleted_by", "restorable_until",
] as const;
const PRIVATE_FIELDS = new Set<string>(EXCLUDED_RULE_FIELDS);

/**
 * D3-D6 fields consumers should present at workflow level when shared: order
 * and limits, placement, terminal/failure actions, and the guard condition.
 * OpenAPI exposes open rule bodies; names come from culture_rules/model/rule.py.
 * sharedValues still compares every non-excluded stored field.
 */
export const SHAREABLE_RULE_FIELDS = [
  "must_after", "may_after", "supersedes", "exclusive_group", "priority",
  "concurrency_key", "max_attempts", "counts_toward_budget",
  "placement", "action", "on_failure", "condition",
] as const;

/**
 * Compare top-level stored fields across every owning rule. Unknown API fields
 * participate too. Missing values stay undefined; null/defaults are not silently
 * equated. Empty groups have no shared values. Identity/lifecycle never fan out.
 */
export function sharedValues(rules: readonly Rule[]): Record<string, SharedValue> {
  const fields = new Set(rules.flatMap((rule) => Object.keys(rule)));
  return Object.fromEntries([...fields].filter((field) => !PRIVATE_FIELDS.has(field)).map((field) => {
    const values = rules.map((rule) => ({
      ruleId: rule.id, value: (rule as unknown as Record<string, unknown>)[field],
    }));
    const value: SharedValue = values.every((item) => identical(item.value, values[0].value))
      ? { kind: "shared", value: values[0].value } : { kind: "differs", values };
    return [field, value];
  }));
}

/** Undirected connected components of explicit continuation links, including cycles. */
function chainsFor(workflows: FoldWorkflow[], entries: EntryPoint[], continuations: Continuation[]): Chain[] {
  const neighbours = new Map(workflows.map((workflow) => [workflow.id, new Set<string>()]));
  for (const entry of continuations) {
    const from = entry.fromWorkflowId;
    // A dangling source remains on its continuation but is not a stored workflow.
    if (from === null || !neighbours.has(from)) continue;
    neighbours.get(from)!.add(entry.workflowId);
    neighbours.get(entry.workflowId)!.add(from);
  }
  const visited = new Set<string>();
  const chains: Chain[] = [];
  for (const workflow of workflows) {
    if (visited.has(workflow.id)) continue;
    const component = new Set<string>();
    const pending = [workflow.id];
    while (pending.length) {
      const id = pending.pop()!;
      if (component.has(id)) continue;
      component.add(id);
      visited.add(id);
      pending.push(...neighbours.get(id)!);
    }
    chains.push({
      workflowIds: workflows.filter((wf) => component.has(wf.id)).map((wf) => wf.id),
      entryPoints: entries.filter((entry) => component.has(entry.workflowId)),
      continuations: continuations.filter((entry) => component.has(entry.workflowId)),
    });
  }
  return chains;
}

export function foldModel(rules: readonly Rule[], workflows: readonly Workflow[]): FoldModel {
  const groups = new Map<string, FoldWorkflow>(workflows.map((workflow) => [workflow.id, {
    id: workflow.id, workflow, entries: [], shared: {},
  }]));
  const entryPoints: EntryPoint[] = [];
  const continuations: Continuation[] = [];
  const d7Candidates: Rule[] = [];
  for (const rule of rules) {
    if (!rule.workflow?.id) {
      d7Candidates.push(rule);
      continue;
    }
    const id = rule.workflow.id;
    if (!groups.has(id)) groups.set(id, { id, workflow: null, entries: [], shared: {} });
    const entry = entryFor(rule, id);
    groups.get(id)!.entries.push(entry);
    if (entry.kind === "entry") entryPoints.push(entry);
    else continuations.push(entry);
  }
  const folded = [...groups.values()];
  for (const group of folded) group.shared = sharedValues(group.entries.map((entry) => entry.rule));
  return {
    workflows: folded, entryPoints, continuations, d7Candidates,
    chains: chainsFor(folded, entryPoints, continuations),
  };
}
