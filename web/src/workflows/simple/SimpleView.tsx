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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLiveUpdates, type LiveChange } from "../../api/live";
import type { Placement, Rule } from "../../api/types";
import type { WorkflowDef } from "../../api/workflows";
import { foldModel, predecessorTerms, type Continuation, type FoldEntry } from "../../fold/model";
import { deleteUnusedWorkflow, type SharedRuleEdit } from "../../fold/writes";
import { AddStageForm, NewRuleForm } from "../../rules/Forms";
import { StopRunsNotice } from "../../rules/StopRunsNotice";
import { useRulesData } from "../../rules/useRulesData";
import "../../rules/rules.css";
import { D7Offer } from "./D7Offer";
import { ConditionRows, EntryCard, type Override } from "./EntryCard";
import { PlacementForm } from "./SharedForms";
import { useFocusReturn } from "./focus";
import { canonical, conditionRows, countsEdit, fieldOf, placementWords, split, triggerParts, valueText, withTerm, withoutEqualTerm } from "./text";
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

/**
 * The Simple view of one workflow. Keyed by the workflow id inside, so opening another workflow
 * starts fresh (its first entry open, no earlier save results or baselines) without the caller
 * having to remember a `key`.
 */
export function SimpleView(props: Readonly<SimpleViewProps>) {
  return <WorkflowSimple key={props.workflowId} {...props} />;
}

function WorkflowSimple({
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

  // Until the reader chooses, the asked-for entry (else the first) is open from the very first
  // paint with rules; the effect only records it, so asks load for it too.
  const openId = open !== undefined ? open : (entries.find((e) => e.rule.id === entry)?.rule.id ?? entries[0]?.rule.id ?? null);
  useEffect(() => {
    if (open === undefined && openId) setOpen(openId);
  }, [open, openId]);

  // A D7 candidate named by `entry` keeps its offer on screen through the writes that end its candidacy.
  const [candidate, setCandidate] = useState<Rule | null>(null);
  const liveCandidate = model.d7Candidates.find((r) => r.id === entry) ?? null;
  useEffect(() => {
    if (liveCandidate && candidate?.id !== liveCandidate.id) setCandidate(liveCandidate);
  }, [liveCandidate, candidate]);

  const fanout = useFanout(refreshRules);
  // Per field, the value the last shared edit wrote: until the next one, the rules that did not
  // take it (a failed or skipped write) are the overrides, showing their old value.
  const [baselines, setBaselines] = useState<Record<string, { value: unknown }>>({});
  const [placing, setPlacing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [addingShared, setAddingShared] = useState(false);
  const placeButton = useFocusReturn<HTMLButtonElement>(placing);
  const createButton = useFocusReturn<HTMLButtonElement>(creating);
  const sharedAddButton = useFocusReturn<HTMLButtonElement>(addingShared);
  const undoButton = useRef<HTMLButtonElement>(null);
  const [focusEntry, setFocusEntry] = useState<string | null>(null);
  const [deleted, setDeleted] = useState<{ rule: Rule; last: boolean } | null>(null);
  const [workflowNote, setWorkflowNote] = useState<string | null>(null);

  const splits = useMemo(
    () => new Map(OVERRIDE_FIELDS.map((f) => [f, split(rules, f, baselines[f])])),
    [rules, baselines],
  );
  const conditionShared = rules.length > 1 && rules.every((r) => r.condition && canonical(r.condition) === canonical(rules[0].condition));
  const placement = splits.get("placement")!;
  const types = [...new Set(rules.map((r) => (r.trigger.kind === "event" ? triggerParts(r.trigger).value : "")))];
  const onward = model.continuations.filter(
    (c) => c.workflowId !== workflowId && (c.fromWorkflowId === workflowId || c.predecessor.kind === "any"
      || (c.predecessor.kind === "ambiguous" && c.predecessor.workflowIds.includes(workflowId))),
  );

  const fanOut = (label: string, edit: Record<string, unknown>) => {
    setBaselines((b) => ({ ...b, ...Object.fromEntries(Object.entries(edit).map(([f, value]) => [f, { value }])) }));
    void fanout.fanOut(label, rules, edit as SharedRuleEdit);
  };
  /**
   * A shared condition edit, computed per rule from that rule's own condition, so each keeps its
   * own data.workflow_id predecessor term (c32), also when re-applied to a rule changed meanwhile.
   */
  const conditionEach = (next: (rule: Rule) => Rule["condition"]) =>
    void fanout.fanOutEach("Condition", ["condition"], rules, (rule) => ({ condition: next(rule) }) as SharedRuleEdit);
  /** One entry's own value: a one-rule write through the fold writes, so it becomes an override. */
  const override = (rule: Rule, label: string, edit: Record<string, unknown>) =>
    void fanout.fanOut(label, [rule], edit as SharedRuleEdit);
  const counts = (rule: Rule) => {
    const edit = countsEdit(rule);
    if (edit) override(rule, `${rule.name}: attempt counting`, edit);
  };
  const predecessor = (rule: Rule, id: string) => void fanout.predecessor(`${rule.name}: continues from`, rule, id);

  const remove = async (rule: Rule) => {
    const last = entries.length === 1 && entries[0].rule.id === rule.id;
    if (!(await data.remove(rule))) return;
    setDeleted({ rule, last });
    requestAnimationFrame(() => undoButton.current?.focus());
    if (openId === rule.id) setOpen(entries.find((e) => e.rule.id !== rule.id)?.rule.id ?? null);
  };
  const undo = async () => {
    if (!deleted) return;
    const doc = await data.restore(deleted.rule);
    if (doc) {
      setDeleted(null);
      setOpen(doc.id);
      setFocusEntry(doc.id);
    }
  };
  const deleteWorkflow = async () => {
    const name = def?.name ?? workflowId;
    const result = await deleteUnusedWorkflow(workflowId);
    if (result.status === "deleted") {
      setDeleted(null);
      setWorkflowNote(`Deleted the workflow ${name}.`);
      onWorkflowDeleted?.(workflowId);
    } else if (result.status === "in-use") {
      setDeleted(null);
      const users = result.rules.map((r) => r.name);
      setWorkflowNote(`Kept the workflow ${name}: ${users.join(", ")} still ${users.length === 1 ? "uses" : "use"} it.`);
      refreshRules();
    } else {
      setWorkflowNote(`Could not delete ${workflowId}: ${result.error instanceof Error ? result.error.message : String(result.error)}`);
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

  // After an undo, focus lands on the restored entry point's own toggle.
  useEffect(() => {
    if (!focusEntry) return;
    const button = document.querySelector<HTMLElement>(`[data-rule-id="${CSS.escape(focusEntry)}"] .fold-entry__expand`);
    if (!button) return;
    button.focus();
    setFocusEntry(null);
  });

  const alerts = [...data.loadErrors, ...(data.notice ? [data.notice] : [])];
  // A shared condition's continuation term (identical on every entry) is never a removable row (c32).
  const sharedCondition = conditionShared ? conditionRows(rules[0].condition, predecessorTerms(rules[0].condition)) : [];

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
          <button ref={undoButton} type="button" className="btn" onClick={() => void undo()}>
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
            <button ref={createButton} type="button" className="fold-add fold-add--big" aria-expanded={creating} onClick={() => setCreating((c) => !c)}>
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
              <button
                ref={placeButton}
                type="button"
                className="fold-shared__placement"
                aria-expanded={placing}
                onClick={() => setPlacing((p) => !p)}
              >
                {placement.shared || placement.baseline
                  ? `evaluates ${placementWords(placement.value as Placement | null | undefined)}`
                  : "evaluates: differs per entry point"}{" "}
                <span aria-hidden="true">▾</span>
              </button>
              <span className="fold-shared__count">{overrideCount(placement.overrides.length)}</span>
              {placing ? (
                <PlacementForm
                  value={placement.value as Placement | null | undefined}
                  mixed={!placement.shared && !placement.baseline}
                  machines={data.machines}
                  busy={fanout.busy}
                  onSave={(next) => {
                    setPlacing(false);
                    fanOut("Placement", { placement: next });
                  }}
                  onCancel={() => setPlacing(false)}
                />
              ) : null}
              {conditionShared ? (
                <>
                  <ConditionRows
                    rows={sharedCondition}
                    label="Every entry point only if all of"
                    busy={fanout.busy}
                    onRemove={(row) => {
                      sharedAddButton.current?.focus();
                      conditionEach((rule) => withoutEqualTerm(rule.condition, row.node!, predecessorTerms(rule.condition)));
                    }}
                  />
                  <button
                    ref={sharedAddButton}
                    type="button"
                    className="fold-add"
                    aria-label="Add a condition for every entry point"
                    aria-expanded={addingShared}
                    onClick={() => setAddingShared((a) => !a)}
                  >
                    <span aria-hidden="true">+</span> condition for every entry point
                  </button>
                  {addingShared ? (
                    <AddStageForm
                      rule={rules[0]}
                      workflows={data.workflows}
                      choice="condition"
                      onSave={async (next) => {
                        conditionEach((rule) => withTerm(rule.condition, next.condition!));
                        return true;
                      }}
                      onCancel={() => setAddingShared(false)}
                    />
                  ) : null}
                </>
              ) : null}
            </div>
          ) : null}

          {entries.map((e) => (
            <EntryCard
              key={e.rule.id}
              entry={e}
              expanded={openId === e.rule.id}
              onExpand={(next) => setOpen(next ? e.rule.id : null)}
              data={data}
              conditionShared={conditionShared}
              overrides={overridesOf(e.rule, splits)}
              busy={fanout.busy}
              historyTick={historyTick}
              onCounts={counts}
              onPredecessor={predecessor}
              onDelete={(rule) => void remove(rule)}
              onOverride={override}
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
          baselines={baselines}
        />
      </div>
    </section>
  );
}

export default SimpleView;
