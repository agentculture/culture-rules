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
  return terms(condition).filter((node) => !skip.includes(node)).map((node) => rowOf(node));
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
  return typeof value === "number" ? `${value} attempt${value === 1 ? "" : "s"} per key` : "no attempt limit";
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
    case "workflow":
      return (value as { id?: string } | null | undefined)?.id ?? "no workflow";
    default:
      return value === undefined ? "unset" : JSON.stringify(value);
  }
}

/** JSON wire identity: object key order is irrelevant, array order is not; `undefined` is its own value. */
export function canonical(value: unknown): string {
  if (value === undefined) return "undefined";
  return JSON.stringify(value, (_key, item: unknown) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : item,
  );
}

export interface Split {
  /** The value most entry points hold (ties: the first entry's), shown once for the workflow. */
  value: unknown;
  /** The entry points holding something else: overrides, each with its own value. */
  overrides: { rule: Rule; value: unknown }[];
}

/** Split one field across a workflow's entry points into its shared value and the overrides. */
export function split(rules: readonly Rule[], field: string): Split {
  if (rules.length === 0) return { value: undefined, overrides: [] };
  const groups = new Map<string, Rule[]>();
  for (const rule of rules) {
    const key = canonical(fieldOf(rule, field));
    groups.set(key, [...(groups.get(key) ?? []), rule]);
  }
  let best = canonical(fieldOf(rules[0], field));
  for (const [key, members] of groups) if (members.length > groups.get(best)!.length) best = key;
  return {
    value: fieldOf(groups.get(best)![0], field),
    overrides: rules.filter((rule) => canonical(fieldOf(rule, field)) !== best)
      .map((rule) => ({ rule, value: fieldOf(rule, field) })),
  };
}
