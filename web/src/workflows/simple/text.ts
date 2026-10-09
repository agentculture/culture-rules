/**
 * The Simple view's words for stored rule values (canvas Fold-Editor): a
 * condition as "Only if all of" rows, an action by its label, a placement as
 * "on spark2", and the majority/override split that shows a value once for
 * the workflow and the entries that differ as overrides (D3-D6). Pure: no
 * React, no network.
 */
import type { Action, Condition, Operand, Placement, Rule, Trigger } from "../../api/types";

/** A stored rule field read by name; open API bodies carry fields the types do not name. */
export const fieldOf = (rule: Rule, field: string): unknown => (rule as unknown as Record<string, unknown>)[field];

export type OperandKind = "field" | "var" | "literal" | "words";

export interface ConditionRow {
  /** The stored all-term this row shows (removing a row removes exactly this node). */
  node?: Condition;
  not: boolean;
  left: string;
  leftKind: OperandKind;
  op: string;
  right: string;
  rightKind: OperandKind;
}

/** `data.head_repo` reads as `head_repo`: the trigger's own data is the default subject. */
function operand(op: Operand): { text: string; kind: OperandKind } {
  if ("field" in op) return { text: op.field.replace(/^data\./, ""), kind: "field" };
  if ("var" in op) return { text: `vars.${op.var}`, kind: "var" };
  return { text: JSON.stringify(op.literal) ?? "null", kind: "literal" };
}

const CMP: Record<string, string> = { "==": "=", "!=": "≠", "<=": "≤", ">=": "≥" };

function rowOf(node: Condition, not = false): ConditionRow {
  switch (node.op) {
    case "compare": {
      const left = operand(node.left);
      const right = operand(node.right);
      return { not, left: left.text, leftKind: left.kind, op: CMP[node.cmp] ?? node.cmp, right: right.text, rightKind: right.kind };
    }
    case "in": {
      const left = operand(node.value);
      const right = operand(node.items);
      return { not, left: left.text, leftKind: left.kind, op: "in", right: right.text, rightKind: right.kind };
    }
    case "exists": {
      const left = operand(node.arg);
      return { not, left: left.text, leftKind: left.kind, op: "exists", right: "", rightKind: "words" };
    }
    case "matches": {
      const left = operand(node.value);
      return { not, left: left.text, leftKind: left.kind, op: "matches", right: JSON.stringify(node.pattern), rightKind: "literal" };
    }
    case "not":
      return node.arg.op === "not" || node.arg.op === "and" || node.arg.op === "or"
        ? { not: !not, left: `${node.arg.op} of ${countOf(node.arg)} conditions`, leftKind: "words", op: "", right: "", rightKind: "words" }
        : rowOf(node.arg, !not);
    default:
      return {
        not,
        left: node.op === "or" ? `any of ${node.args.length} conditions` : `all of ${node.args.length} conditions`,
        leftKind: "words",
        op: "",
        right: "",
        rightKind: "words",
      };
  }
}

function countOf(node: Condition): number {
  return node.op === "and" || node.op === "or" ? node.args.length : 1;
}

/**
 * The rows an entry point shows under "Only if all of": the top-level
 * all-terms, flattened through nested `and`. `skip` drops the nodes shown
 * elsewhere (a continuation's predecessor term has its own control).
 */
export function conditionRows(condition: Condition | null | undefined, skip: readonly Condition[] = []): ConditionRow[] {
  if (!condition) return [];
  const terms = (node: Condition): Condition[] => (node.op === "and" ? node.args.flatMap(terms) : [node]);
  return terms(condition).filter((node) => !skip.includes(node)).map((node) => ({ ...rowOf(node), node }));
}

/** The condition with one more all-term: a bare condition becomes the first of an `and`. */
export function withTerm(condition: Condition | null | undefined, term: Condition): Condition {
  if (!condition) return term;
  // Already one of its all-terms: adding it again would only duplicate it.
  const flat = (node: Condition): Condition[] => (node.op === "and" ? node.args.flatMap(flat) : [node]);
  if (flat(condition).some((node) => canonical(node) === canonical(term))) return condition;
  if (condition.op === "and") return { ...condition, args: [...condition.args, term] };
  return { op: "and", args: [condition, term] };
}

/**
 * The condition without exactly this all-term node (found through nested `and`s); every other
 * node, a continuation's data.workflow_id term included, is kept as stored. An `and` left with
 * one term becomes that term; with none, there is no condition.
 */
export function withoutTerm(condition: Condition | null | undefined, node: Condition): Condition | null {
  if (!condition || condition === node) return null;
  if (condition.op !== "and") return condition;
  const args = condition.args
    .map((arg) => withoutTermIn(arg, node))
    .filter((arg): arg is Condition => arg !== null);
  if (args.length === 0) return null;
  return args.length === 1 ? args[0] : { ...condition, args };
}

/** One argument of an `and` without `node`: gone if it is the node, recursed into if an `and`. */
function withoutTermIn(arg: Condition, node: Condition): Condition | null {
  if (arg === node) return null;
  return arg.op === "and" ? withoutTerm(arg, node) : arg;
}

/** A row as plain words, for one-line summaries ("verdict in [...]"). */
export function rowText(row: ConditionRow): string {
  return [row.not ? "not" : "", row.left, row.op, row.right].filter(Boolean).join(" ");
}

/** The trigger's kind and its one telling value (event type, cron, probe command or legacy label). */
export function triggerParts(trigger: Trigger): { kind: string; value: string } {
  const params = (trigger.params ?? {}) as Record<string, unknown>;
  const pick = ["type", "cron", "command", "label", "event"].map((key) => params[key]).find((v) => typeof v === "string" && v);
  return { kind: trigger.kind, value: typeof pick === "string" ? pick : "" };
}

/** `rules.run.succeeded` → "succeeds": the run event a continuation waits for, in words. */
export function runEventWords(trigger: Trigger): string {
  const type = triggerParts(trigger).value;
  if (type === "rules.run.succeeded") return "succeeds";
  if (type === "rules.run.failed") return "fails";
  if (type.startsWith("rules.run.")) return `ends (${type.slice("rules.run.".length)})`;
  return `sends ${type}`;
}

export function actionText(action: Action | null | undefined): string {
  if (!action) return "nothing";
  return action.name?.trim() || action.kind;
}

/** What the action posts, when it posts text: the body or text param, as stored. */
export function actionBody(action: Action | null | undefined): string | null {
  const params = (action?.params ?? {}) as Record<string, unknown>;
  const body = params.body ?? params.text ?? params.message;
  return typeof body === "string" ? body : null;
}

/** "on spark2", "via qwen-fixer", "by capability", "anywhere". */
export function placementWords(placement: Placement | null | undefined): string {
  if (placement?.machine) return `on ${placement.machine}`;
  if (placement?.actor) return `via ${placement.actor}`;
  if (placement?.requirement?.length) return "by capability";
  return "anywhere";
}

export function attemptsText(value: unknown): string {
  if (typeof value !== "number") return "no attempt limit";
  const noun = value === 1 ? "attempt" : "attempts";
  return `${value} ${noun} per key`;
}

/** One stored value in words, by field (the old value a failed save keeps, an override). */
export function valueText(field: string, value: unknown): string {
  switch (field) {
    case "action":
    case "on_failure":
      return actionText(value as Action | null | undefined);
    case "placement":
      return `evaluates ${placementWords(value as Placement | null | undefined)}`;
    case "concurrency_key":
      return typeof value === "string" && value ? value : "no run key";
    case "max_attempts":
      return attemptsText(value);
    case "counts_toward_budget":
      return value === false ? "does not count toward the attempt budget" : "counts toward the attempt budget";
    case "condition": {
      const rows = conditionRows(value as Condition | null | undefined).map(rowText);
      return rows.length ? rows.join(" and ") : "no condition";
    }
    case "workflow":
      return (value as { id?: string } | null | undefined)?.id ?? "no workflow";
    default:
      return value === undefined ? "unset" : JSON.stringify(value);
  }
}

function compareKeys(a: string, b: string): number {
  if (a < b) return -1;
  return a > b ? 1 : 0;
}

/** JSON wire identity: object key order is irrelevant, array order is not; `undefined` is its own value. */
export function canonical(value: unknown): string {
  if (value === undefined) return "undefined";
  return JSON.stringify(value, (_key, item: unknown) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => compareKeys(a, b)))
      : item,
  );
}

export interface Split {
  /** Every entry point holds the value identically (D3-D6: shown once, no overrides). */
  shared: boolean;
  /**
   * The value shown for the workflow: the shared one, or the value a shared edit just wrote
   * (`baseline`) while some rules did not take it. Undefined when the entries simply differ.
   */
  value: unknown;
  /** True when `value` is a baseline from the last shared edit rather than everyone's value. */
  baseline: boolean;
  /** The entry points showing their own value: those off the baseline, or all when they differ. */
  overrides: { rule: Rule; value: unknown }[];
}

/**
 * Split one field across a workflow's entry points, "shared when identical" (D3-D6): a value
 * is shared only when every entry holds it identically. Otherwise each entry shows its own value
 * as an override — except that after a shared edit, the value it wrote is the baseline and only
 * the rules that did not take it (a failed or skipped write) are overrides, with their old value.
 */
export function split(rules: readonly Rule[], field: string, baseline?: { value: unknown }): Split {
  const all = rules.map((rule) => ({ rule, value: fieldOf(rule, field) }));
  if (all.length === 0) return { shared: false, value: undefined, baseline: false, overrides: [] };
  const first = canonical(all[0].value);
  if (all.every((item) => canonical(item.value) === first)) {
    return { shared: true, value: all[0].value, baseline: false, overrides: [] };
  }
  if (baseline) {
    const wanted = canonical(baseline.value);
    if (all.some((item) => canonical(item.value) === wanted)) {
      return { shared: false, value: baseline.value, baseline: true, overrides: all.filter((item) => canonical(item.value) !== wanted) };
    }
  }
  return { shared: false, value: undefined, baseline: false, overrides: all };
}

/**
 * The condition without its first all-term equal (as JSON) to `term`, found through nested
 * `and`s: how a term removed from a shared condition leaves each rule's own condition. A
 * data.workflow_id predecessor term is never removed. Unchanged when the rule has no such term.
 */
export function withoutEqualTerm(condition: Condition | null | undefined, term: Condition, keep: readonly Condition[] = []): Condition | null {
  const wanted = canonical(term);
  const flat = (node: Condition): Condition[] => (node.op === "and" ? node.args.flatMap(flat) : [node]);
  const found = condition ? flat(condition).find((node) => !keep.includes(node) && canonical(node) === wanted) : undefined;
  return found ? withoutTerm(condition, found) : (condition ?? null);
}

/** The run fields a Runs edit may change; only the ones the author changed are sent. */
export interface RunsEdit {
  concurrency_key?: string | null;
  max_attempts?: number | null;
}

const outsideBudget = (rule: Rule) => fieldOf(rule, "counts_toward_budget") === false;

/**
 * Why the server would refuse this Runs edit on these rules (culture_rules/model/validate.py):
 * a rule outside the attempt budget (`counts_toward_budget: false`) needs a run key and may not
 * set `max_attempts`; a budget is at least 1. Null when every rule can take it.
 */
const KEY_PLACEHOLDER = /^trigger(?:\.[A-Za-z0-9_-]+)+$/;

/**
 * Why the server would refuse this run-key template (validate.py `_concurrency_key_problem`):
 * only `{trigger.<path>}` placeholders, balanced braces (`{{` and `}}` escape one), no format
 * spec or conversion (`:` or `!` inside a placeholder). Null when it is valid.
 */
export function runKeyProblem(template: string): string | null {
  if (!template.trim()) return "A run key must not be empty.";
  const unescaped = template.replaceAll("{{", "").replaceAll("}}", "");
  if (/\{[^{}]*[:!]/.test(unescaped)) return "A run key's placeholders take no format specs and conversions (: or !).";
  let i = 0;
  while (i < template.length) {
    const step = runKeyStep(template, i);
    if (typeof step === "string") return step;
    i = step;
  }
  return null;
}

/** One scan step of a run-key template at `i`: the next index, or the problem found there. */
function runKeyStep(template: string, i: number): number | string {
  const c = template[i];
  if ((c === "{" || c === "}") && template[i + 1] === c) return i + 2;
  if (c === "}") return "A run key has unbalanced braces: a single } (write }} for a literal one).";
  if (c !== "{") return i + 1;
  const end = template.indexOf("}", i + 1);
  if (end < 0) return "A run key has unbalanced braces: a { is never closed.";
  const name = template.slice(i + 1, end);
  if (name.includes("{")) return "A run key has unbalanced braces: a { inside a placeholder.";
  if (!KEY_PLACEHOLDER.test(name)) return `A run key takes only {trigger.<path>} placeholders, not {${name}}.`;
  return end + 1;
}

export function runsProblem(rules: readonly Rule[], edit: RunsEdit): string | null {
  const own = editProblem(edit);
  if (own) return own;
  const outside = rules.filter(outsideBudget);
  if (outside.length === 0) return null;
  return outsideBudgetProblem(outside, edit);
}

/** What is wrong with the edit's own values, whatever the rules. */
function editProblem(edit: RunsEdit): string | null {
  if (typeof edit.concurrency_key === "string") {
    const bad = runKeyProblem(edit.concurrency_key);
    if (bad) return bad;
  }
  if (typeof edit.max_attempts === "number" && (!Number.isInteger(edit.max_attempts) || edit.max_attempts < 1)) {
    return "An attempt budget is a whole number, at least 1.";
  }
  return null;
}

/** Why rules outside the attempt budget (at least one) cannot take this edit. */
function outsideBudgetProblem(outside: readonly Rule[], edit: RunsEdit): string | null {
  const names = outside.map((r) => r.name).join(", ");
  const one = outside.length === 1;
  if (typeof edit.max_attempts === "number") {
    const verb = one ? "does" : "do";
    const subject = one ? "it" : "they";
    const object = one ? "it" : "them";
    return `${names} ${verb} not count toward the attempt budget, so ${subject} cannot take one. Count ${object} first.`;
  }
  if ("concurrency_key" in edit && edit.concurrency_key === null) {
    return `${names} ${one ? "is" : "are"} outside the attempt budget, which needs a run key.`;
  }
  return null;
}

/**
 * The attempt-counting switch's edit, as the server's validation requires: opting out needs a
 * run key and drops the rule's own `max_attempts` (a rule outside the budget cannot set one);
 * opting back in only sets the flag. Null when the switch cannot change (no run key to opt out of).
 */
export function countsEdit(rule: Rule): Record<string, unknown> | null {
  if (outsideBudget(rule)) return { counts_toward_budget: true };
  const key = fieldOf(rule, "concurrency_key");
  if (typeof key !== "string" || !key) return null;
  return { counts_toward_budget: false, max_attempts: null };
}
