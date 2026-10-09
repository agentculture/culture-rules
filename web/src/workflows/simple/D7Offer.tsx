import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../api/client";
import { failureMessage } from "../../api/settle";
import { getRule } from "../../api/rules";
import type { Rule } from "../../api/types";
import type { WorkflowDef } from "../../api/workflows";
import { createD7Workflow, deleteWorkflowDoc, saveSharedEdit, type RuleWriteResult } from "../../fold/writes";
import { slugFor } from "../../routes/rules-view";

const errorText = (err: unknown) => (err instanceof ApiError ? err.message : failureMessage(err));

type State =
  | { phase: "idle"; message?: string }
  | { phase: "busy" }
  | { phase: "saved"; workflow: Pick<WorkflowDef, "id" | "name"> }
  | { phase: "orphan"; orphan: WorkflowDef; snapshot: Rule; message: string };

function attachFailure(rule: Rule, workflowId: string, result: Exclude<RuleWriteResult, { status: "saved" }>): string {
  if (result.status === "skipped-changed") return `${rule.name} changed since you opened it, so it was not pointed at ${workflowId}`;
  return `${rule.name} could not be pointed at ${workflowId} (${errorText(result.error)})`;
}

export interface D7OfferProps {
  rule: Rule;
  /** Workflow ids already in use: the suggested id avoids them. */
  takenIds: readonly string[];
  hrefFor: (workflowId: string) => string;
  onCreated?: (workflowId: string) => void;
  /** Run the two writes at once, without waiting for a click (D7 for a just-created rule). */
  autoStart?: boolean;
}

/**
 * Where the rule points now. An orphan can hide a committed attach (a lost response whose
 * reconciliation read failed), so every orphan step re-reads the rule before it acts.
 */
async function reread(ruleId: string): Promise<{ rule: Rule | null; error: unknown }> {
  try {
    return { rule: await getRule(ruleId), error: null };
  } catch (error) {
    return { rule: null, error };
  }
}

/**
 * D7 for a rule with no workflow (spec c30): offer to give it a stored
 * workflow of its own, with no steps yet — its trigger and condition become
 * the When, its action the Then. Two writes through the fold writes: create
 * the workflow, then point the rule at it. If the second fails the new
 * workflow is deleted again; if that fails too it is flagged as an orphan
 * with the actions that fix it (attach again, or delete it).
 */
export function D7Offer({ rule, takenIds, hrefFor, onCreated, autoStart = false }: Readonly<D7OfferProps>) {
  const [name, setName] = useState(rule.name);
  const [used, setUsed] = useState<string[]>([]);
  const [id, setId] = useState(() => slugFor(rule.name, [...takenIds], "workflow"));
  const [state, setState] = useState<State>({ phase: "idle" });
  const busy = state.phase === "busy";

  const done = (workflow: Pick<WorkflowDef, "id" | "name">) => {
    setState({ phase: "saved", workflow });
    onCreated?.(workflow.id);
  };

  const create = async () => {
    const wrapper = { id: id.trim(), name: name.trim() || rule.name };
    if (!wrapper.id) return;
    setState({ phase: "busy" });
    const result = await createD7Workflow(rule, wrapper);
    if (result.status === "saved") return done(result.workflow);
    if (!("ruleResult" in result)) {
      return setState({ phase: "idle", message: `Could not create ${wrapper.id}: ${errorText(result.error)}` });
    }
    if (!result.workflow) {
      return setState({ phase: "idle", message: `${attachFailure(rule, wrapper.id, result.ruleResult)}; nothing was created.` });
    }
    const why = attachFailure(rule, result.workflow.id, result.ruleResult);
    if (result.cleanup === "deleted") {
      // A soft-deleted id stays taken: suggest a fresh one for the retry.
      const taken = [...used, result.workflow.id];
      setUsed(taken);
      setId(slugFor(wrapper.name, [...takenIds, ...taken], "workflow"));
      return setState({ phase: "idle", message: `${why}. The new workflow ${result.workflow.id} was deleted again.` });
    }
    // The attach may have committed after all: look before calling it an orphan.
    const now = await reread(rule.id);
    if (now.rule?.workflow?.id === result.orphan.id) return done(result.orphan);
    setState({
      phase: "orphan",
      orphan: result.orphan,
      snapshot: now.rule ?? result.ruleResult.snapshot,
      message: `${result.orphan.id} was created but ${rule.name} does not use it: ${why}. Deleting it failed too (${errorText(result.cleanupError)}).`,
    });
  };

  const attach = async (orphan: WorkflowDef, snapshot: Rule) => {
    setState({ phase: "busy" });
    // Re-read: the attach may have landed meanwhile, and the write must start from what is stored.
    const now = await reread(rule.id);
    if (now.rule?.workflow?.id === orphan.id) return done(orphan);
    if (!now.rule) {
      return setState({ phase: "orphan", orphan, snapshot, message: `${orphan.id} is still unused: ${rule.name} could not be re-read (${errorText(now.error)}).` });
    }
    const [result] = await saveSharedEdit([now.rule], { workflow: { id: orphan.id } });
    if (result.status === "saved") return done(orphan);
    setState({ phase: "orphan", orphan, snapshot: now.rule, message: `${orphan.id} is still unused: ${attachFailure(rule, orphan.id, result)}.` });
  };

  const removeOrphan = async (orphan: WorkflowDef, snapshot: Rule) => {
    setState({ phase: "busy" });
    // Never delete a workflow the rule turns out to use.
    const now = await reread(rule.id);
    if (now.rule?.workflow?.id === orphan.id) return done(orphan);
    if (!now.rule) {
      return setState({ phase: "orphan", orphan, snapshot, message: `${orphan.id} was kept: ${rule.name} could not be re-read to check it is unused (${errorText(now.error)}).` });
    }
    const removed = await deleteWorkflowDoc(orphan.id);
    if (removed.status === "deleted") return setState({ phase: "idle", message: `Deleted the unused workflow ${orphan.id}.` });
    setState({ phase: "orphan", orphan, snapshot: now.rule, message: `${orphan.id} is still unused; deleting it failed (${errorText(removed.error)}).` });
  };

  const started = useRef(false);
  useEffect(() => {
    // Once per mount, even under StrictMode's double effects: a second run would create twice.
    if (!autoStart || started.current) return;
    started.current = true;
    void create();
  }, [autoStart]);

  return (
    <section className="fold-d7" aria-label={`${rule.name} has no workflow`}>
      <h2 className="fold-d7__title">{rule.name} has no workflow</h2>
      {state.phase === "saved" ? (
        <p className="fold-d7__done">
          {rule.name} now starts {state.workflow.name}.{" "}
          <Link to={hrefFor(state.workflow.id)}>Open {state.workflow.name}</Link>
        </p>
      ) : (
        <>
          <p className="fold-d7__lead">
            Give it a workflow of its own, with no steps yet: its trigger and condition become the When, its action the Then.
            It runs as it does now.
          </p>
          <div className="fold-d7__fields">
            <label>
              <span>Workflow name</span>
              <input value={name} disabled={busy} onChange={(e) => setName(e.target.value)} />
            </label>
            <label>
              <span>Workflow id</span>
              <input value={id} disabled={busy} onChange={(e) => setId(e.target.value)} />
            </label>
          </div>
          {state.phase === "orphan" ? (
            <div className="notice notice--error fold-d7__orphan" role="alert">
              <span>{state.message}</span>
              <button type="button" className="btn btn--primary" onClick={() => attach(state.orphan, state.snapshot)}>
                Attach {rule.name} to {state.orphan.id}
              </button>
              <button type="button" className="btn" onClick={() => removeOrphan(state.orphan, state.snapshot)}>
                Delete {state.orphan.id}
              </button>
            </div>
          ) : (
            <>
              {state.phase === "idle" && state.message ? <p className="fold-d7__message">{state.message}</p> : null}
              <button type="button" className="btn btn--primary" disabled={busy || !id.trim()} onClick={() => void create()}>
                Create its workflow
              </button>
            </>
          )}
        </>
      )}
    </section>
  );
}
