/** Fold writes return data for the Simple view's overrides/retry controls. */
import { ApiError } from "../api/client";
import { createRule, getRule, SERVER_MANAGED_RULE_FIELDS, updateRule } from "../api/rules";
import type { Condition, Rule } from "../api/types";
import { predecessorTerms } from "./model";
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
export type SharedRuleEdit = Partial<Omit<Rule, "id" | "name" | "description" | "enabled" | "schema_version" | typeof SERVER_MANAGED_RULE_FIELDS[number]>> & {
  id?: never;
  name?: never;
  description?: never;
  enabled?: never;
  schema_version?: never;
} & Partial<Record<typeof SERVER_MANAGED_RULE_FIELDS[number], never>>;

/** JSON wire equality: object order is irrelevant; array order and all fields matter. */
function canonical(value: unknown): string {
  return JSON.stringify(value, (_key, item: unknown) => {
    if (item && typeof item === "object" && !Array.isArray(item)) {
      return Object.fromEntries(Object.entries(item).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0));
    }
    return item;
  });
}

async function checkAttempt(attempt: RuleAttempt): Promise<UnsuccessfulRuleWrite | null> {
  let current: Rule;
  try {
    current = await getRule(attempt.ruleId);
  } catch (error) {
    return { ...attempt, status: "failed", phase: "read", error };
  }
  if (canonical(current) !== canonical(attempt.snapshot)) {
    return { ...attempt, status: "skipped-changed", current };
  }
  return null;
}

async function saveAttempt(attempt: RuleAttempt): Promise<RuleWriteResult> {
  const checked = await checkAttempt(attempt);
  if (checked) return checked;
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
 * fields ride along, except server-managed metadata omitted by updateRule. Retry failed entries with
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
  const forbidden = ["id", "name", "description", "enabled", "schema_version", ...SERVER_MANAGED_RULE_FIELDS].some((key) => key in edit);
  const results: RuleWriteResult[] = [];
  for (const attempt of attempts) {
    results.push(forbidden
      ? { ...attempt, status: "failed", phase: "prepare", error: new Error("Identity, lifecycle, schema and server-managed fields cannot be shared") }
      : await saveAttempt(attempt));
  }
  return results;
}

type UnsuccessfulRuleWrite = Exclude<RuleWriteResult, { status: "saved" }>;
export type D7WriteResult =
  | { status: "saved"; workflow: WorkflowDef; ruleResult: Extract<RuleWriteResult, { status: "saved" }> }
  | { status: "failed"; phase: "prepare" | "create"; error: unknown }
  | { status: "failed" | "skipped-changed"; ruleResult: UnsuccessfulRuleWrite; workflow?: never; cleanup?: never }
  | ({ status: "failed" | "skipped-changed"; workflow: WorkflowDef; ruleResult: UnsuccessfulRuleWrite } & (
      | { cleanup: "deleted" }
      | { cleanup: "orphan"; orphan: WorkflowDef; cleanupError: unknown }
    ));

/**
 * D7 for an existing workflow-less rule. The caller supplies a new workflow id;
 * a failed create (including an id collision) must never trigger a delete.
 * Rollback uses the API's soft delete. An orphan includes the id needed to retry
 * cleanup, plus both the attachment result and the reconciliation/cleanup failure.
 */
export async function createD7Workflow(
  snapshot: Rule,
  wrapper: Pick<WorkflowDef, "id" | "name">,
): Promise<D7WriteResult> {
  const original = structuredClone(snapshot);
  if (original.workflow) {
    return { status: "failed", phase: "prepare", error: new Error("D7 requires a rule without a workflow") };
  }
  const attempt = {
    ruleId: original.id, snapshot: original,
    attempted: { ...original, workflow: { id: wrapper.id } },
  };
  const checked = await checkAttempt(attempt);
  if (checked) return { status: checked.status, ruleResult: checked };
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
  // A lost response or server error may hide a committed attachment. Soft
  // delete permits referenced workflows, so reconcile before attempting cleanup.
  if (ruleResult.status === "failed" && ruleResult.phase === "write"
    && ruleResult.error instanceof ApiError
    && (ruleResult.error.status === 0 || (ruleResult.error.status >= 500 && ruleResult.error.status < 600))) {
    try {
      const current = await getRule(original.id);
      if (current.workflow?.id === workflow.id) {
        return { status: "saved", workflow, ruleResult: { ...ruleResult, status: "saved", rule: current } };
      }
    } catch (cleanupError) {
      // Preserve the wrapper and both errors when attachment is unconfirmed.
      return { ...result, cleanup: "orphan", orphan: workflow, cleanupError };
    }
  }
  try {
    await deleteWorkflowDef(workflow.id);
    return { ...result, cleanup: "deleted" };
  } catch (cleanupError) {
    return { ...result, cleanup: "orphan", orphan: workflow, cleanupError };
  }
}

/**
 * Rewrites one positive all-term equality only, never terms inside `or`/`not`.
 * The term is found with the model's `predecessorTerms`, so the editor can
 * rewrite exactly the links the fold model shows as linked.
 */
function rewritePredecessor(condition: Condition | null | undefined, predecessorId: string): Condition {
  const terms = predecessorTerms(condition);
  if (!condition || terms.length !== 1) {
    throw new Error("Changing a predecessor requires exactly one all-term data.workflow_id equality");
  }
  const [term] = terms;
  const field = "field" in term.left ? "right" : "left";
  function visit(node: Condition): Condition {
    if (node === term) return { ...node, [field]: { ...node[field], literal: predecessorId } };
    return node.op === "and" ? { ...node, args: node.args.map(visit) } : node;
  }
  return visit(condition);
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

export type RuleCreateResult = { status: "saved"; rule: Rule } | { status: "failed"; error: unknown };

/** Create a rule through the existing `POST /rules`, as the Rules tab did; returns data, never throws. */
export async function createRuleDoc(doc: Rule): Promise<RuleCreateResult> {
  try {
    return { status: "saved", rule: await createRule(doc) };
  } catch (error) {
    return { status: "failed", error };
  }
}

export type WorkflowDeleteResult =
  | { status: "deleted"; workflowId: string }
  | { status: "failed"; workflowId: string; error: unknown };

/** Soft-delete a workflow (`DELETE /workflows/{id}`); returns data, never throws. */
export async function deleteWorkflowDoc(workflowId: string): Promise<WorkflowDeleteResult> {
  try {
    await deleteWorkflowDef(workflowId);
    return { status: "deleted", workflowId };
  } catch (error) {
    return { status: "failed", workflowId, error };
  }
}
