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
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
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
import { useFrozen } from "./freeze";
import { canonical, conditionRows, countsEdit, fieldOf, placementWords, split, triggerParts, valueText, withTerm, withoutEqualTerm, type Split } from "./text";
import { ThenColumn } from "./ThenColumn";
import { useFeedSubscription, type LiveFeed } from "./liveFeed";
import { useFanout } from "./useFanout";
import { WriteResults } from "./WriteResults";
import "./simple.css";

const LIVE_COLLECTIONS = ["rules", "runs", "asks", "rule_decisions"] as const;
/** With a feed from the page, no stream of its own (an empty list opens none). */
const NO_COLLECTIONS: readonly string[] = [];

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
  /**
   * Live changes from the page's own stream (it must carry rules, runs, asks and
   * rule_decisions). Given, the view opens no EventSource of its own; absent, it opens one.
   */
  live?: LiveFeed;
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
  if (n === 0) return "";
  const noun = n === 1 ? "override" : "overrides";
  return ` · ${n} ${noun}`;
}

/** The continuations that start after this workflow: linked to it, after any, or naming it among several. */
function onwardOf(continuations: readonly Continuation[], workflowId: string): Continuation[] {
  return continuations.filter(
    (c) => c.workflowId !== workflowId && (c.fromWorkflowId === workflowId || c.predecessor.kind === "any"
      || (c.predecessor.kind === "ambiguous" && c.predecessor.workflowIds.includes(workflowId))),
  );
}

/** The entry open until the reader chooses: the asked-for one if it is here, else the first. */
function defaultOpenId(entries: readonly FoldEntry[], entry: string | null | undefined): string | null {
  return entries.find((e) => e.rule.id === entry)?.rule.id ?? entries[0]?.rule.id ?? null;
}

/** The one event type every entry point listens for, if there is exactly one. */
const soleType = (types: readonly string[]) => (types.length === 1 && types[0] ? types[0] : undefined);

const emptyWords = (found: boolean, workflowId: string) => (found ? "No rule starts this workflow yet." : `No workflow ${workflowId}.`);

const errorMessage = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** What a workflow delete did, in words. */
function workflowDeleteNote(result: Awaited<ReturnType<typeof deleteUnusedWorkflow>>, name: string, workflowId: string): string {
  if (result.status === "deleted") return `Deleted the workflow ${name}.`;
  if (result.status === "in-use") {
    const users = result.rules.map((r) => r.name);
    const verb = users.length === 1 ? "uses" : "use";
    return `Kept the workflow ${name}: ${users.join(", ")} still ${verb} it.`;
  }
  return `Could not delete ${workflowId}: ${errorMessage(result.error)}`;
}

/** The undo notice after an entry point is deleted, with the D7 workflow delete when offered (c30). */
function DeletedNotice({
  name,
  workflowName,
  offerWorkflowDelete,
  undoButton,
  onUndo,
  onDeleteWorkflow,
  onDismiss,
}: Readonly<{
  name: string;
  workflowName: string;
  offerWorkflowDelete: boolean;
  undoButton: RefObject<HTMLButtonElement>;
  onUndo: () => void;
  onDeleteWorkflow: () => void;
  onDismiss: () => void;
}>) {
  return (
    <output className="notice notice--undo">
      <span>Deleted {name}</span>
      <button ref={undoButton} type="button" className="btn" onClick={onUndo}>
        Undo
      </button>
      {offerWorkflowDelete ? (
        <button type="button" className="btn" onClick={onDeleteWorkflow}>
          Delete the workflow {workflowName} too
        </button>
      ) : null}
      <button type="button" className="icon-button icon-button--small" aria-label="Dismiss" onClick={onDismiss}>
        ×
      </button>
    </output>
  );
}

/**
 * "Shared by every entry point": the placement and, when every entry holds it, the condition,
 * each edited once and fanned out. Rendered always (empty without rules) so its open forms
 * keep their state as before.
 */
function SharedBlock({
  rules,
  data,
  baselines,
  placement,
  conditionShared,
  busy,
  fanOut,
  conditionEach,
}: Readonly<{
  rules: Rule[];
  data: ReturnType<typeof useRulesData>;
  baselines: Record<string, { value: unknown }>;
  placement: Split;
  conditionShared: boolean;
  busy: boolean;
  fanOut: (label: string, edit: Record<string, unknown>, snapshots?: readonly Rule[]) => void;
  conditionEach: (next: (rule: Rule) => Rule["condition"], snapshots?: readonly Rule[]) => void;
}>) {
  const [placing, setPlacing] = useState(false);
  const [addingShared, setAddingShared] = useState(false);
  const placeButton = useFocusReturn<HTMLButtonElement>(placing);
  const sharedAddButton = useFocusReturn<HTMLButtonElement>(addingShared);
  // The placement and shared-condition forms keep what they were opened on (c27).
  const placeAt = useFrozen(placing ? "placement" : null, { rules, placement: split(rules, "placement", baselines.placement) });
  const conditionAt = useFrozen(addingShared ? "shared-condition" : null, rules);
  if (rules.length === 0) return null;
  // A shared condition's continuation term (identical on every entry) is never a removable row (c32).
  const sharedCondition = conditionShared ? conditionRows(rules[0].condition, predecessorTerms(rules[0].condition)) : [];
  return (
    <fieldset aria-label="Shared by every entry point" className="fold-shared plain-group">
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
          value={placeAt.placement.value as Placement | null | undefined}
          mixed={!placeAt.placement.shared && !placeAt.placement.baseline}
          machines={data.machines}
          busy={busy}
          onSave={(next) => {
            setPlacing(false);
            fanOut("Placement", { placement: next }, placeAt.rules);
          }}
          onCancel={() => setPlacing(false)}
        />
      ) : null}
      {conditionShared ? (
        <>
          <ConditionRows
            rows={sharedCondition}
            label="Every entry point only if all of"
            busy={busy}
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
              onSave={(next) => {
                conditionEach((rule) => withTerm(rule.condition, next.condition!), conditionAt);
                return Promise.resolve(true);
              }}
              onCancel={() => setAddingShared(false)}
            />
          ) : null}
        </>
      ) : null}
    </fieldset>
  );
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
  live: feed,
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
  const live = useLiveUpdates(feed ? NO_COLLECTIONS : LIVE_COLLECTIONS, onLive);
  useFeedSubscription(feed, onLive);

  const model = useMemo(() => foldModel(data.rules, data.workflows), [data.rules, data.workflows]);
  const folded = model.workflows.find((w) => w.id === workflowId) ?? null;
  const entries: FoldEntry[] = useMemo(() => folded?.entries ?? [], [folded]);
  const rules = useMemo(() => entries.map((e) => e.rule), [entries]);

  // Until the reader chooses, the asked-for entry (else the first) is open from the very first
  // paint with rules; the effect only records it, so asks load for it too.
  const openId = open === undefined ? defaultOpenId(entries, entry) : open;
  useEffect(() => {
    if (open === undefined && openId) setOpen(openId);
  }, [open, openId]);

  // A newly asked entry (an ?entry= link within the same workflow) opens, without a remount.
  const [asked, setAsked] = useState(entry);
  useEffect(() => {
    if (entry === asked) return;
    if (entry && !entries.some((e) => e.rule.id === entry)) return; // not loaded yet: try again
    setAsked(entry);
    if (entry) setOpen(entry);
  }, [entry, asked, entries]);

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
  const [creating, setCreating] = useState(false);
  const createButton = useFocusReturn<HTMLButtonElement>(creating);
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
  const onward = onwardOf(model.continuations as Continuation[], workflowId);

  /** Fan out to `snapshots`: by default the rules shown now, else what the form was opened on. */
  const fanOut = (label: string, edit: Record<string, unknown>, snapshots: readonly Rule[] = rules) => {
    setBaselines((b) => ({ ...b, ...Object.fromEntries(Object.entries(edit).map(([f, value]) => [f, { value }])) }));
    void fanout.fanOut(label, snapshots, edit as SharedRuleEdit);
  };
  /**
   * A shared condition edit, computed per rule from that rule's own condition, so each keeps its
   * own data.workflow_id predecessor term (c32), also when re-applied to a rule changed meanwhile.
   */
  const conditionEach = (next: (rule: Rule) => Rule["condition"], snapshots: readonly Rule[] = rules) =>
    void fanout.fanOutEach("Condition", ["condition"], snapshots, (rule) => ({ condition: next(rule) }) as SharedRuleEdit);
  /** One entry's own value: a one-rule write through the fold writes, so it becomes an override. */
  const override = (rule: Rule, label: string, edit: Record<string, unknown>) =>
    fanout.fanOut(label, [rule], edit as SharedRuleEdit);
  const counts = (rule: Rule) => {
    const edit = countsEdit(rule);
    if (edit) void override(rule, `${rule.name}: attempt counting`, edit);
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
    if (result.status === "deleted" || result.status === "in-use") setDeleted(null);
    setWorkflowNote(workflowDeleteNote(result, name, workflowId));
    if (result.status === "deleted") onWorkflowDeleted?.(workflowId);
    if (result.status === "in-use") refreshRules();
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
        <DeletedNotice
          name={deleted.rule.name}
          workflowName={def?.name ?? workflowId}
          offerWorkflowDelete={Boolean(offerWorkflowDelete)}
          undoButton={undoButton}
          onUndo={() => void undo()}
          onDeleteWorkflow={() => void deleteWorkflow()}
          onDismiss={() => setDeleted(null)}
        />
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

          <SharedBlock
            rules={rules}
            data={data}
            baselines={baselines}
            placement={placement}
            conditionShared={conditionShared}
            busy={fanout.busy}
            fanOut={fanOut}
            conditionEach={conditionEach}
          />

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
            <p className="fold-col__empty">{emptyWords(folded !== null, workflowId)}</p>
          ) : null}
        </section>

        {def ? <Steps def={def} /> : null}

        <ThenColumn
          workflowId={workflowId}
          rules={rules}
          onward={onward}
          workflows={data.workflows}
          actors={data.actors}
          triggerType={soleType(types)}
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
