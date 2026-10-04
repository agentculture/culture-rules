import type { Action, Condition, Operand, Rule, WorkflowRef } from "../api/types";
import type { StageKind } from "../culture-design/stages";

/** The stages a rule draws, in flow order; Condition and Workflow are optional. */
export function stagesOf(rule: Rule): StageKind[] {
  const stages: StageKind[] = ["trigger"];
  if (rule.condition) stages.push("condition");
  if (rule.workflow) stages.push("workflow");
  stages.push("action");
  return stages;
}

export function triggerLabel(rule: Rule): string {
  const params = rule.trigger.params ?? {};
  if (typeof params.label === "string" && params.label) return params.label;
  if (typeof params.event === "string" && params.event) return `${rule.trigger.kind}: ${params.event}`;
  return rule.trigger.kind;
}

const last = (ref: string) => ref.split(".").pop() ?? ref;

export function workflowChips(ref: WorkflowRef): string[] {
  return Object.entries(ref.inputs ?? {}).map(([input, from]) => {
    const text = typeof from === "string" ? from : "$ref" in from ? from.$ref : String(from.$literal);
    return `${last(text)} → ${input}`;
  });
}

const REFERENCE = /^(trigger|workflow|vars|rule)\.[A-Za-z0-9_.]+$/;

export function actionChips(action: Action): string[] {
  return Object.entries(action.params ?? {})
    .filter(([, v]) => typeof v === "string" && REFERENCE.test(v))
    .map(([param, v]) => `${last(v as string)} → ${param}`);
}

export interface ConditionText {
  /** The operand the condition reads (rendered mono), if simple. */
  subject: string | null;
  /** The rest, in words. */
  rest: string;
}

function operandText(op: Operand): string {
  if ("var" in op) return op.var;
  if ("field" in op) return `trigger.${op.field}`;
  return typeof op.literal === "string" ? op.literal : JSON.stringify(op.literal);
}

const CMP_WORDS: Record<string, string> = { "==": "is", "!=": "is not" };

/** A condition tree in words; anything beyond one comparison stays honest and short. */
export function conditionText(c: Condition): ConditionText {
  if (c.op === "compare") {
    return {
      subject: operandText(c.left),
      rest: `${CMP_WORDS[c.cmp] ?? c.cmp} ${operandText(c.right)}`,
    };
  }
  if (c.op === "exists") return { subject: operandText(c.arg), rest: "exists" };
  if (c.op === "matches") return { subject: operandText(c.value), rest: `matches ${c.pattern}` };
  if (c.op === "in") return { subject: operandText(c.value), rest: `is in ${operandText(c.items)}` };
  if (c.op === "and" || c.op === "or") {
    return { subject: null, rest: `${c.args.length} conditions, ${c.op === "and" ? "all" : "any"} must hold` };
  }
  return { subject: null, rest: "not …" };
}

/** `4m`, `1h`, `yesterday`, `3d` — the board's 'Last runs' column. */
export function ago(iso: string | null, now: number): string {
  if (!iso) return "—";
  const minutes = Math.max(0, Math.round((now - Date.parse(iso)) / 60_000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  if (hours < 48) return "yesterday";
  return `${Math.floor(hours / 24)}d`;
}

function operandVars(op: Operand, into: Set<string>) {
  if ("var" in op) into.add(op.var);
}

function conditionVars(c: Condition, into: Set<string>) {
  switch (c.op) {
    case "compare":
      operandVars(c.left, into);
      operandVars(c.right, into);
      break;
    case "and":
    case "or":
      c.args.forEach((a) => conditionVars(a, into));
      break;
    case "not":
      conditionVars(c.arg, into);
      break;
    case "exists":
      operandVars(c.arg, into);
      break;
    case "in":
      operandVars(c.value, into);
      operandVars(c.items, into);
      break;
    case "matches":
      operandVars(c.value, into);
      break;
  }
}

/**
 * The variables this rule reads that the trigger does not supply — the
 * upstream outputs a relationship makes visible (the board's `verdict` chip
 * on the 'must run after' card): `{var}` operands in the condition and
 * `vars.x` references in the workflow inputs and action params.
 */
export function upstreamVars(rule: Rule): string[] {
  const found = new Set<string>();
  if (rule.condition) conditionVars(rule.condition, found);
  const refs = [
    ...Object.values(rule.workflow?.inputs ?? {}),
    ...Object.values(rule.action.params ?? {}),
  ];
  for (const ref of refs) {
    if (typeof ref === "string" && ref.startsWith("vars.")) found.add(ref.slice(5).split(".")[0]);
  }
  return [...found];
}

/** `--a-b--` → `a-b`: leading and trailing dashes dropped. */
export function trimDashes(text: string): string {
  // Index scans, not a `^-+|-+$` regex: that backtracks quadratically on an inner dash run.
  let start = 0;
  let end = text.length;
  while (start < end && text[start] === "-") start++;
  while (end > start && text[end - 1] === "-") end--;
  return text.slice(start, end);
}

/** `Disk is nearly full` → `disk-is-nearly-full`, unique among `taken` (`fallback` when nothing is left). */
export function slugFor(name: string, taken: string[], fallback = "rule"): string {
  const base = trimDashes(name.toLowerCase().replace(/[^a-z0-9]+/g, "-")) || fallback;
  let slug = base;
  let n = 2;
  while (taken.includes(slug)) {
    slug = `${base}-${n}`;
    n++;
  }
  return slug;
}
