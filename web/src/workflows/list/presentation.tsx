import { Link } from "react-router-dom";
import type { Action, Condition, Operand, Rule } from "../../api/types";
import type { Chain, Continuation, FoldEntry, FoldModel, FoldWorkflow } from "../../fold/model";

export const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;
export const workflowName = (model: FoldModel, id: string) =>
  model.workflows.find((wf) => wf.id === id)?.workflow?.name || id || "Empty workflow id";
// Chains have no stored name. Use their first workflow's name, without inferring a product name.
export const chainName = (model: FoldModel, chain: Chain) => workflowName(model, chain.workflowIds[0]);
export function workflowUrl(id: string, entry?: string): string {
  const url = `/workflows?id=${encodeURIComponent(id)}`;
  return entry ? `${url}&entry=${encodeURIComponent(entry)}` : url;
}
export function fromText(entry: Continuation): string {
  if (entry.predecessor.kind === "ambiguous") return "from multiple workflow terms";
  if (entry.predecessor.kind === "any") return "from any workflow";
  return entry.predecessor.workflowId ? `from ${entry.predecessor.workflowId}` : "from an empty workflow id";
}
const valueText = (value: unknown): string => typeof value === "string" && value !== "" ? value : JSON.stringify(value) ?? "Not set";
function operandText(operand: Operand): string {
  if ("field" in operand) return operand.field;
  if ("var" in operand) return `vars.${operand.var}`;
  return valueText(operand.literal);
}
/** A stored trigger param as text: strings as they are, anything else as JSON, never "[object Object]". */
function paramText(value: unknown, fallback: string): string {
  if (value === undefined || value === null) return fallback;
  return typeof value === "string" ? value : JSON.stringify(value);
}
export function guardText(condition: Condition): string {
  switch (condition.op) {
    case "and": case "or": return condition.args.map((term) => `(${guardText(term)})`).join(` ${condition.op} `);
    case "not": return `not (${guardText(condition.arg)})`;
    case "compare": return `${operandText(condition.left)} ${condition.cmp} ${operandText(condition.right)}`;
    case "exists": return `${operandText(condition.arg)} exists`;
    case "in": return `${operandText(condition.value)} in ${operandText(condition.items)}`;
    case "matches": return `${operandText(condition.value)} matches ${condition.pattern}`;
  }
}
export function triggerText(rule: Rule): string {
  const params = rule.trigger.params as Record<string, unknown> | undefined;
  if (rule.trigger.kind === "event") return paramText(params?.type, "Event (type not set)");
  if (rule.trigger.kind === "schedule") return `Schedule: ${paramText(params?.cron, "not set")} ${paramText(params?.tz, "")}`.trim();
  if (rule.trigger.kind === "probe") return `Probe: ${paramText(params?.command, "not set")}`;
  return rule.trigger.kind;
}
export function SameEventNote({ model, entry }: Readonly<{ model: FoldModel; entry: FoldEntry }>) {
  const type = (entry.rule.trigger.params as Record<string, unknown> | undefined)?.type;
  if (entry.kind !== "entry" || typeof type !== "string") return null;
  const ids = [...new Set(model.entryPoints.filter((other) =>
    other.workflowId !== entry.workflowId
    && other.rule.trigger.kind === entry.rule.trigger.kind
    && (other.rule.trigger.params as Record<string, unknown> | undefined)?.type === type).map((other) => other.workflowId))];
  return <>{ids.map((id) => <small key={id}>same event starts {workflowName(model, id)}</small>)}</>;
}
export function EntrySummary({ entry, model, onOpen }: Readonly<{ entry: FoldEntry; model?: FoldModel; onOpen?: (id: string) => void }>) {
  return <div className="fold-entry" data-rule-id={entry.rule.id}>
    <Link to={workflowUrl(entry.workflowId, entry.rule.id)} onClick={() => onOpen?.(entry.workflowId)}>{entry.rule.name}</Link>
    {!entry.enabled && <span className="fold-tag">Disabled</span>}
    <small>{entry.kind === "continuation" ? `${fromText(entry)} · ` : ""}{triggerText(entry.rule)}</small>
    {model && <SameEventNote model={model} entry={entry} />}
    {entry.rule.condition && <details><summary>Guard</summary><p>{guardText(entry.rule.condition)}</p></details>}
    {entry.rule.placement?.machine && <span className="fold-tag">{entry.rule.placement.machine}</span>}
    <small>was {entry.rule.id}</small>
  </div>;
}
function actionText(action: Action | null | undefined): string {
  if (!action) return "Not set";
  const parts = [action.name || action.kind];
  if (action.params?.actor) parts.push(action.params.actor);
  if (action.only_at_chain_end) parts.push("only at chain end");
  return parts.join(" · ");
}
function runsText(rule: Rule): string {
  const stored = rule as unknown as Record<string, unknown>;
  const labels: Record<string, string> = { concurrency_key: "Key", max_attempts: "Max attempts", counts_toward_budget: "Counts toward budget", exclusive_group: "Exclusive group", priority: "Priority" };
  const parts = Object.entries(labels).filter(([key]) => stored[key] !== undefined && stored[key] !== null)
    .map(([key, label]) => `${label}: ${valueText(stored[key])}`);
  for (const [key, label] of [["must_after", "Must run after"], ["may_after", "May run after"], ["supersedes", "Supersedes"]] as const) {
    if (rule[key]?.length) parts.push(`${label}: ${rule[key]!.join(", ")}`);
  }
  return parts.join(" · ") || "No run limits specified";
}
function RuleValues({ workflow, format }: Readonly<{ workflow: FoldWorkflow; format: (rule: Rule) => string }>) {
  const groups = new Map<string, string[]>();
  for (const { rule } of workflow.entries) {
    const text = format(rule);
    groups.set(text, [...(groups.get(text) ?? []), rule.name]);
  }
  if (!groups.size) return <>No entry points</>;
  return <>{[...groups].map(([text, names]) => <p key={text}>
    {groups.size > 1 && <strong>{names.join(", ")}: </strong>}{text}
  </p>)}</>;
}
export function RunSummary({ workflow }: Readonly<{ workflow: FoldWorkflow }>) {
  return <RuleValues workflow={workflow} format={runsText} />;
}
function endText(rule: Rule): string {
  const end = actionText(rule.action);
  return rule.on_failure ? `${end} · On failure: ${actionText(rule.on_failure)}` : end;
}
export function EndSummary({ workflow }: Readonly<{ workflow: FoldWorkflow }>) {
  return <RuleValues workflow={workflow} format={endText} />;
}
