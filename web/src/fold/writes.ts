/** Fold writes return data for the Simple view's overrides/retry controls. */
import { getJson } from "../api/client";
import { updateRule } from "../api/rules";
import type { Condition, Operand, Rule } from "../api/types";
import { createWorkflowDef, deleteWorkflowDef, type WorkflowDef } from "../api/workflows";

interface RuleAttempt {
  ruleId: string;
  snapshot: Rule;
  attempted: Rule;
}
export type RuleWriteResult = RuleAttempt & (
  | { status: "saved"; rule: Rule }
  | { status: "skipped-changed"; current: Rule }
  | { status: "failed"; phase: "prepare" | "read" | "write"; error: unknown }
);

/** Identity and lifecycle are per-entry fields, never shared values. */
export type SharedRuleEdit = Partial<Omit<Rule, "id" | "name" | "description" | "enabled">> & {
  id?: never;
  name?: never;
  description?: never;
  enabled?: never;
};

/** JSON wire equality: object order is irrelevant; array order and all fields matter. */
function canonical(value: unknown): string {
  return JSON.stringify(value, (_key, item: unknown) => {
    if (item && typeof item === "object" && !Array.isArray(item)) {
      return Object.fromEntries(Object.entries(item).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0));
    }
    return item;
  });
}

async function saveAttempt(attempt: RuleAttempt): Promise<RuleWriteResult> {
  let current: Rule;
  try {
    current = await getJson<Rule>(`/rules/${encodeURIComponent(attempt.ruleId)}`);
  } catch (error) {
    return { ...attempt, status: "failed", phase: "read", error };
  }
  if (canonical(current) !== canonical(attempt.snapshot)) {
    return { ...attempt, status: "skipped-changed", current };
  }
  // The existing endpoint has no If-Match support. This check cannot close
  // the race between GET and PUT; never claim an atomic compare-and-swap.
  try {
    return { ...attempt, status: "saved", rule: await updateRule(attempt.attempted) };
  } catch (error) {
    return { ...attempt, status: "failed", phase: "write", error };
  }
}

/**
 * Replace the supplied shared fields on each snapshot, sequentially. Unedited
 * fields (including unknown server fields) ride along. Retry failed entries with
 * their snapshot; a skipped entry needs review and a fresh snapshot first.
 */
export async function saveSharedEdit(
  snapshots: readonly Rule[],
  edit: SharedRuleEdit,
): Promise<RuleWriteResult[]> {
  // Capture every input before the first await so UI changes cannot alter a batch.
  const attempts = snapshots.map((snapshot) => ({
    ruleId: snapshot.id,
    snapshot: structuredClone(snapshot),
    attempted: structuredClone({ ...snapshot, ...edit }),
  }));
  const forbidden = ["id", "name", "description", "enabled"].some((key) => key in edit);
  const results: RuleWriteResult[] = [];
  for (const attempt of attempts) {
    results.push(forbidden
      ? { ...attempt, status: "failed", phase: "prepare", error: new Error("Identity and lifecycle fields cannot be shared") }
      : await saveAttempt(attempt));
  }
  return results;
}

type UnsuccessfulRuleWrite = Exclude<RuleWriteResult, { status: "saved" }>;
export type D7WriteResult =
  | { status: "saved"; workflow: WorkflowDef; ruleResult: Extract<RuleWriteResult, { status: "saved" }> }
  | { status: "failed"; phase: "prepare" | "create"; error: unknown }
  | ({ status: "failed" | "skipped-changed"; workflow: WorkflowDef; ruleResult: UnsuccessfulRuleWrite } & (
      | { cleanup: "deleted" }
      | { cleanup: "orphan"; orphan: WorkflowDef; cleanupError: unknown }
    ));

/**
 * D7 for an existing workflow-less rule. The caller supplies a new workflow id;
 * a failed create (including an id collision) must never trigger a delete.
 * Rollback uses the API's soft delete. An orphan includes the id needed to retry
 * cleanup, plus both the attachment result and the cleanup failure.
 */
export async function createD7Workflow(
  snapshot: Rule,
  wrapper: Pick<WorkflowDef, "id" | "name">,
): Promise<D7WriteResult> {
  const original = structuredClone(snapshot);
  if (original.workflow) {
    return { status: "failed", phase: "prepare", error: new Error("D7 requires a rule without a workflow") };
  }
  let workflow: WorkflowDef;
  try {
    workflow = await createWorkflowDef({ id: wrapper.id, name: wrapper.name, steps: [], edges: [] });
  } catch (error) {
    return { status: "failed", phase: "create", error };
  }
  const ruleResult = await saveAttempt({
    ruleId: original.id, snapshot: original,
    attempted: { ...original, workflow: { id: workflow.id } },
  });
  if (ruleResult.status === "saved") return { status: "saved", workflow, ruleResult };
  const result = { status: ruleResult.status, workflow, ruleResult };
  try {
    await deleteWorkflowDef(workflow.id);
    return { ...result, cleanup: "deleted" };
  } catch (cleanupError) {
    return { ...result, cleanup: "orphan", orphan: workflow, cleanupError };
  }
}

function isWorkflowField(operand: Operand): boolean {
  return "field" in operand && operand.field === "data.workflow_id";
}
function isWorkflowId(operand: Operand): boolean {
  return "literal" in operand && typeof operand.literal === "string";
}

/** Rewrites one positive all-term equality only, never terms inside `or`/`not`. */
function rewritePredecessor(condition: Condition | null | undefined, predecessorId: string): Condition {
  let matches = 0;
  function visit(node: Condition): Condition {
    if (node.op === "and") return { ...node, args: node.args.map(visit) };
    if (node.op !== "compare" || node.cmp !== "==") return node;
    if (isWorkflowField(node.left) && isWorkflowId(node.right)) {
      matches++;
      return { ...node, right: { ...node.right, literal: predecessorId } };
    }
    if (isWorkflowField(node.right) && isWorkflowId(node.left)) {
      matches++;
      return { ...node, left: { ...node.left, literal: predecessorId } };
    }
    return node;
  }
  const rewritten = condition && visit(condition);
  if (!rewritten || matches !== 1) {
    throw new Error("Changing a predecessor requires exactly one all-term data.workflow_id equality");
  }
  return rewritten;
}

/** Dedicated predecessor save; unrelated predicates and rule fields stay intact. */
export async function savePredecessor(snapshot: Rule, predecessorId: string): Promise<RuleWriteResult> {
  const original = structuredClone(snapshot);
  const attempt = { ruleId: original.id, snapshot: original, attempted: structuredClone(original) };
  try {
    attempt.attempted.condition = rewritePredecessor(attempt.attempted.condition, predecessorId);
  } catch (error) {
    return { ...attempt, status: "failed", phase: "prepare", error };
  }
  return saveAttempt(attempt);
}
