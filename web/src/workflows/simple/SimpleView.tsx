/**
 * The Simple view (canvas Fold-Editor, spec c9): a workflow as When / Then.
 *
 *   When — every rule that starts this workflow, as an entry point (its
 *          trigger, condition, placement, attempt counting and enable switch);
 *          continuations are entry points too, owned by the workflow they
 *          start (D2).
 *   Then — continues into (read-only links to the workflows that start after
 *          this one), ends here (the chain-end action), on failure, and runs
 *          (run key and attempt budget).
 *
 * The data is the fold model (web/src/fold/model.ts) over the same rules,
 * workflows, machines and actors the Rules tab loaded (useRulesData). Writes:
 *
 *   - one entry point's fields go through the Rules tab's own forms and calls
 *     (RuleEditForm, AddStageForm, relationships, enable/disable), so a save
 *     is the same rule document the Rules tab made, and touches that rule only;
 *   - a value shared by every entry point (D3-D6) is edited once and fanned out
 *     through the fold writes (web/src/fold/writes.ts), rule by rule, with each
 *     rule's result shown — a failure keeps its old value as an override and
 *     offers a retry; a rule changed meanwhile is skipped and flagged;
 *   - a continuation's predecessor and an entry's attempt counting go through
 *     the fold writes too (savePredecessor, a one-rule shared edit);
 *   - a rule with no workflow is offered D7: a stepless workflow of its own.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useLiveUpdates, type LiveChange } from "../../api/live";
import type { Placement, Rule } from "../../api/types";
import { deleteWorkflowDef, type WorkflowDef } from "../../api/workflows";
import { foldModel, type Continuation, type FoldEntry } from "../../fold/model";
import type { SharedRuleEdit } from "../../fold/writes";
import { NewRuleForm } from "../../rules/Forms";
import { StopRunsNotice } from "../../rules/StopRunsNotice";
import { useRulesData } from "../../rules/useRulesData";
import "../../rules/rules.css";
import { D7Offer } from "./D7Offer";
import { ConditionRows, EntryCard, type Override } from "./EntryCard";
import { PlacementForm } from "./SharedForms";
import { conditionRows, fieldOf, placementWords, split, triggerParts, valueText } from "./text";
import { ThenColumn } from "./ThenColumn";
import { useFanout } from "./useFanout";
import { WriteResults } from "./WriteResults";
import "./simple.css";

const LIVE_COLLECTIONS = ["rules", "runs", "asks", "rule_decisions"] as const;

/** The fields an entry shows as an override when it differs from the workflow's value. */
const OVERRIDE_FIELDS = ["placement", "action", "on_failure", "concurrency_key", "max_attempts"] as const;

export interface SimpleViewProps {
  workflowId: string;
  /** The workflow's full definition, when the caller has it: its steps fill the middle column. */
  def?: WorkflowDef | null;
  /** The entry point to open first (`?entry=<rule id>`); a workflow-less rule's id shows its D7 offer. */
  entry?: string | null;
  /** Where a link to a workflow goes. */
  hrefFor?: (workflowId: string) => string;
  /** A D7 offer created this workflow for its rule. */
  onCreated?: (workflowId: string) => void;
  /** The workflow was deleted from here (after its last entry point went). */
  onWorkflowDeleted?: (workflowId: string) => void;
}

const defaultHref = (id: string) => `/workflows?id=${encodeURIComponent(id)}`;

/** The steps between When and Then: Start, each step with where it runs, End. */
function Steps({ def }: Readonly<{ def: WorkflowDef }>) {
  const steps = def.steps ?? [];
  return (
    <section className="fold-col fold-col--steps" aria-label="Steps of this workflow">
      <ol className="fold-steps" aria-label="Steps">
        <li className="fold-step fold-step--start" aria-label="Start">
          <span aria-hidden="true">▶</span> Start
        </li>
        {steps.map((step) => (
          <li key={step.id} className="fold-step" aria-label={step.name ?? step.id}>
            <span className="fold-step__name">{step.name ?? step.id}</span>
            <span className="fold-step__meta">
              <span className="fold-term fold-term--field">{step.kind}</span>
              <span className="fold-badge">{placementWords(step.placement).replace(/^on /, "")}</span>
            </span>
          </li>
        ))}
        {steps.length === 0 ? <li className="fold-step__empty">No steps yet: the entry points' actions are the whole run.</li> : null}
        <li className="fold-step fold-step--end" aria-label="End">
          End
        </li>
      </ol>
    </section>
  );
}

/** The words for the shared placement button and its override count. */
function overrideCount(n: number) {
  return n === 0 ? "" : ` · ${n} override${n === 1 ? "" : "s"}`;
}

/** The overrides one entry carries: its own value of each D3-D6 field that differs. */
function overridesOf(rule: Rule, splits: Map<string, ReturnType<typeof split>>): Override[] {
  return OVERRIDE_FIELDS.flatMap((field) => {
    const s = splits.get(field);
    if (!s?.overrides.some((o) => o.rule.id === rule.id)) return [];
    const text = valueText(field, fieldOf(rule, field));
    return [{ field, text: field === "placement" ? text : `${labelOf(field)}: ${text}` }];
  });
}

const LABELS: Record<string, string> = {
  action: "ends here",
  on_failure: "on failure",
  concurrency_key: "run key",
  max_attempts: "attempts",
};
const labelOf = (field: string) => LABELS[field] ?? field;

export function SimpleView({
  workflowId,
  def,
  entry,
  hrefFor = defaultHref,
  onCreated,
  onWorkflowDeleted,
}: Readonly<SimpleViewProps>) {
  // undefined: not chosen yet (the asked-for entry, else the first, once the rules are in).
  const [open, setOpen] = useState<string | null | undefined>(undefined);
  const data = useRulesData(open ?? entry ?? undefined);
  const { refreshRules, refreshAsks } = data;
  const [historyTick, setHistoryTick] = useState(0);
  const onLive = useCallback((changes: LiveChange[]) => {
    const touched = new Set(changes.map((c) => c.collection));
    if (touched.has("rules")) refreshRules();
    if (touched.has("runs") || touched.has("asks")) refreshAsks();
    if (touched.has("runs") || touched.has("rule_decisions")) setHistoryTick((n) => n + 1);
  }, [refreshRules, refreshAsks]);
  const live = useLiveUpdates(LIVE_COLLECTIONS, onLive);

  const model = useMemo(() => foldModel(data.rules, data.workflows), [data.rules, data.workflows]);
  const folded = model.workflows.find((w) => w.id === workflowId) ?? null;
  const entries: FoldEntry[] = useMemo(() => folded?.entries ?? [], [folded]);
  const rules = useMemo(() => entries.map((e) => e.rule), [entries]);

  useEffect(() => {
    if (open !== undefined || entries.length === 0) return;
    setOpen(entries.find((e) => e.rule.id === entry)?.rule.id ?? entries[0].rule.id);
  }, [open, entries, entry]);

  // A D7 candidate named by `entry` keeps its offer on screen through the writes that end its candidacy.
  const [candidate, setCandidate] = useState<Rule | null>(null);
  const liveCandidate = model.d7Candidates.find((r) => r.id === entry) ?? null;
  useEffect(() => {
    if (liveCandidate && candidate?.id !== liveCandidate.id) setCandidate(liveCandidate);
  }, [liveCandidate, candidate]);

  const fanout = useFanout(refreshRules);
  const [placing, setPlacing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deleted, setDeleted] = useState<{ rule: Rule; last: boolean } | null>(null);
  const [workflowNote, setWorkflowNote] = useState<string | null>(null);

  const splits = useMemo(() => new Map(OVERRIDE_FIELDS.map((f) => [f, split(rules, f)])), [rules]);
  const conditionShared = rules.length > 1 && rules.every((r) => r.condition && JSON.stringify(r.condition) === JSON.stringify(rules[0].condition));
  const placement = splits.get("placement")!;
  const attempts = splits.get("max_attempts")!.value;
  const types = [...new Set(rules.map((r) => (r.trigger.kind === "event" ? triggerParts(r.trigger).value : "")))];
  const onward = model.continuations.filter(
    (c) => c.workflowId !== workflowId && (c.fromWorkflowId === workflowId || c.predecessor.kind === "any"
      || (c.predecessor.kind === "ambiguous" && c.predecessor.workflowIds.includes(workflowId))),
  );

  const fanOut = (label: string, edit: Record<string, unknown>) =>
    void fanout.fanOut(label, rules, edit as SharedRuleEdit);
  const counts = (rule: Rule) =>
    void fanout.fanOut(`${rule.name}: attempt counting`, [rule], {
      counts_toward_budget: fieldOf(rule, "counts_toward_budget") === false,
    } as SharedRuleEdit);
  const predecessor = (rule: Rule, id: string) => void fanout.predecessor(`${rule.name}: continues from`, rule, id);

  const remove = async (rule: Rule) => {
    const last = entries.length === 1 && entries[0].rule.id === rule.id;
    if (!(await data.remove(rule))) return;
    setDeleted({ rule, last });
    if (open === rule.id) setOpen(entries.find((e) => e.rule.id !== rule.id)?.rule.id ?? null);
  };
  const undo = async () => {
    if (!deleted) return;
    const doc = await data.restore(deleted.rule);
    if (doc) {
      setDeleted(null);
      setOpen(doc.id);
    }
  };
  const deleteWorkflow = async () => {
    try {
      await deleteWorkflowDef(workflowId);
      setDeleted(null);
      setWorkflowNote(`Deleted the workflow ${def?.name ?? workflowId}.`);
      onWorkflowDeleted?.(workflowId);
    } catch (err) {
      setWorkflowNote(`Could not delete ${workflowId}: ${err instanceof Error ? err.message : String(err)}`);
    }
  };
  // Deleting the last entry point of a stepless (D7) workflow offers to delete the workflow too (c30).
  const offerWorkflowDelete = deleted?.last && entries.length === 0 && def && (def.steps ?? []).length === 0;

  const onCreate = async (doc: Rule) => {
    const made = await data.create({ ...doc, workflow: { id: workflowId, inputs: {} } });
    if (made) {
      setCreating(false);
      setOpen(made.id);
    }
    return made !== null;
  };

  const alerts = [...data.loadErrors, ...(data.notice ? [data.notice] : [])];
  const sharedCondition = conditionShared ? conditionRows(rules[0].condition) : [];

  return (
    <section className="fold-simple" aria-label="Simple view" data-live-flash={live.flash || undefined}>
      {alerts.length > 0 ? (
        <p className="notice notice--error" role="alert">
          {alerts.join(" · ")}
        </p>
      ) : null}
      {data.stopOffer ? (
        <StopRunsNotice offer={data.stopOffer} onApprove={() => void data.stopRuns()} onDismiss={data.dismissStop} />
      ) : null}
      {deleted ? (
        <output className="notice notice--undo">
          <span>Deleted {deleted.rule.name}</span>
          <button type="button" className="btn" onClick={() => void undo()}>
            Undo
          </button>
          {offerWorkflowDelete ? (
            <button type="button" className="btn" onClick={() => void deleteWorkflow()}>
              Delete the workflow {def?.name ?? workflowId} too
            </button>
          ) : null}
          <button type="button" className="icon-button icon-button--small" aria-label="Dismiss" onClick={() => setDeleted(null)}>
            ×
          </button>
        </output>
      ) : null}
      {workflowNote ? <output className="notice">{workflowNote}</output> : null}
      {fanout.batch ? (
        <WriteResults batch={fanout.batch} busy={fanout.busy} onRetry={(id) => void fanout.retry(id)} onDismiss={fanout.dismiss} />
      ) : null}
      {candidate ? (
        <D7Offer
          rule={liveCandidate ?? candidate}
          takenIds={data.workflows.map((w) => w.id)}
          hrefFor={hrefFor}
          onCreated={(id) => {
            refreshRules();
            onCreated?.(id);
          }}
        />
      ) : null}

      <div className="fold-board">
        <section className="fold-col fold-col--when" aria-labelledby={`${workflowId}-when`}>
          <div className="fold-col__head">
            <h2 id={`${workflowId}-when`} className="fold-col__title">When</h2>
            <button type="button" className="fold-add fold-add--big" aria-pressed={creating} onClick={() => setCreating((c) => !c)}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
                <path d="M12 5v14M5 12h14" />
              </svg>
              Entry point
            </button>
          </div>
          {creating ? (
            <NewRuleForm
              actors={data.actors}
              takenIds={data.rules.map((r) => r.id)}
              onCancel={() => setCreating(false)}
              onCreate={onCreate}
            />
          ) : null}

          {rules.length > 0 ? (
            <div role="group" aria-label="Shared by every entry point" className="fold-shared">
              <button type="button" className="fold-shared__placement" aria-expanded={placing} onClick={() => setPlacing((p) => !p)}>
                evaluates {placementWords(placement.value as Placement | null | undefined)} <span aria-hidden="true">▾</span>
              </button>
              <span className="fold-shared__count">{overrideCount(placement.overrides.length)}</span>
              {placing ? (
                <PlacementForm
                  value={placement.value as Placement | null | undefined}
                  machines={data.machines}
                  busy={fanout.busy}
                  onSave={(next) => {
                    setPlacing(false);
                    fanOut("Placement", { placement: next });
                  }}
                  onCancel={() => setPlacing(false)}
                />
              ) : null}
              <ConditionRows rows={sharedCondition} label="Every entry point only if all of" />
            </div>
          ) : null}

          {entries.map((e) => (
            <EntryCard
              key={e.rule.id}
              entry={e}
              expanded={open === e.rule.id}
              onExpand={(next) => setOpen(next ? e.rule.id : null)}
              data={data}
              conditionShared={conditionShared}
              overrides={overridesOf(e.rule, splits)}
              attempts={attempts}
              busy={fanout.busy}
              historyTick={historyTick}
              onCounts={counts}
              onPredecessor={predecessor}
              onDelete={(rule) => void remove(rule)}
            />
          ))}
          {data.loaded && entries.length === 0 ? (
            <p className="fold-col__empty">{folded ? "No rule starts this workflow yet." : `No workflow ${workflowId}.`}</p>
          ) : null}
        </section>

        {def ? <Steps def={def} /> : null}

        <ThenColumn
          workflowId={workflowId}
          rules={rules}
          onward={onward as Continuation[]}
          workflows={data.workflows}
          actors={data.actors}
          triggerType={types.length === 1 && types[0] ? types[0] : undefined}
          busy={fanout.busy}
          hrefFor={hrefFor}
          onFanOut={fanOut}
        />
      </div>
    </section>
  );
}

export default SimpleView;
