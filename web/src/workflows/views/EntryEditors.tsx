/**
 * The entry-point and Then-side editors, in Detailed (spec c13, task t15).
 *
 * Simple is text only, so Detailed carries every editor it dropped. They are imported from
 * ../simple, not copied: EntryCard (the entry trigger, condition, placement and attempt counting,
 * continues from, enable and delete), PlacementForm and AddStageForm (placement and condition
 * shared by every entry point) and ThenColumn (continues into, ends here, on failure, runs).
 *
 * The glue here is the old Simple view's (SimpleView.tsx before t14), ported whole by t23 (d10):
 *
 *   - live updates: the page's feed (or a stream of its own, without one) refreshes the rules, the
 *     asks and the entry history; an open form keeps the snapshot it was opened on (c27);
 *   - a shared edit fans out rule by rule through the fold writes; the rules that did not take
 *     it are the overrides, counted on the shared panel;
 *   - disabling an entry point with runs going asks "Stop N current runs?" (StopRunsNotice);
 *   - deleting an entry point offers Undo (focused), opens the next entry point, and on the last
 *     entry point of a stepless workflow offers to delete the workflow too (c30); Undo focuses
 *     the restored entry point;
 *   - `entry` (?entry=<rule>) opens that entry point, also when it changes later, without a
 *     remount; another workflow starts fresh (keyed by its id).
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
import { ConditionRows, EntryCard, type Override } from "../simple/EntryCard";
import { useFocusReturn } from "../simple/focus";
import { useFrozen } from "../simple/freeze";
import { useFeedSubscription, type LiveFeed } from "../simple/liveFeed";
import { PlacementForm } from "../simple/SharedForms";
import { ThenColumn } from "../simple/ThenColumn";
import { useFanout } from "../simple/useFanout";
import { WriteResults } from "../simple/WriteResults";
import {
  canonical, conditionRows, countsEdit, fieldOf, placementWords, split, triggerParts, valueText, withTerm,
  withoutEqualTerm, type Split,
} from "../simple/text";
import "../simple/simple.css";

const LIVE_COLLECTIONS = ["rules", "runs", "asks", "rule_decisions"] as const;
/** With a feed from the page, no stream of its own (an empty list opens none). */
const NO_COLLECTIONS: readonly string[] = [];

/** The fields an entry shows as an override when it differs from the workflow's value. */
const OVERRIDE_FIELDS = ["placement", "action", "on_failure", "concurrency_key", "max_attempts"] as const;
const LABELS: Record<string, string> = {
  action: "ends here",
  on_failure: "on failure",
  concurrency_key: "run key",
  max_attempts: "attempts",
};

const defaultHref = (id: string) => `/workflows?id=${encodeURIComponent(id)}`;
const errorMessage = (err: unknown) => (err instanceof Error ? err.message : String(err));

function overridesOf(rule: Rule, splits: Map<string, ReturnType<typeof split>>): Override[] {
  return OVERRIDE_FIELDS.flatMap((field) => {
    const s = splits.get(field);
    if (!s?.overrides.some((o) => o.rule.id === rule.id)) return [];
    const text = valueText(field, fieldOf(rule, field));
    return [{ field, text: field === "placement" ? text : `${LABELS[field] ?? field}: ${text}` }];
  });
}

/** The words after the shared placement button: its override count. */
function overrideCount(n: number) {
  if (n === 0) return "";
  return ` · ${n} ${n === 1 ? "override" : "overrides"}`;
}

/** The continuations that start after this workflow: linked to it, after any, naming it among several, or as an OR branch. */
function onwardOf(continuations: readonly Continuation[], workflowId: string): Continuation[] {
  return continuations.filter(
    (c) =>
      c.workflowId !== workflowId &&
      (c.fromWorkflowId === workflowId ||
        c.predecessor.kind === "any" ||
        ((c.predecessor.kind === "ambiguous" || c.predecessor.kind === "either") &&
          c.predecessor.workflowIds.includes(workflowId))),
  );
}

/** The entry open until the reader chooses: the asked-for one if it is here, else the first. */
function defaultOpenId(entries: readonly FoldEntry[], entry: string | null | undefined): string | null {
  return entries.find((e) => e.rule.id === entry)?.rule.id ?? entries[0]?.rule.id ?? null;
}

/** The one event type every entry point listens for, if there is exactly one. */
const soleType = (types: readonly string[]) => (types.length === 1 && types[0] ? types[0] : undefined);

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

/** The undo notice after an entry point is deleted, with the stepless workflow's delete when offered (c30). */
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
 * "Shared by every entry point": the placement (with its override count) and, when every entry
 * holds it, the condition, each edited once and fanned out. Its forms keep what they were opened
 * on (c27) while a live refresh brings newer rules.
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
  const placeAt = useFrozen(placing ? "placement" : null, { rules, placement: split(rules, "placement", baselines.placement) });
  const conditionAt = useFrozen(addingShared ? "shared-condition" : null, rules);
  if (rules.length === 0) return null;
  // A shared condition's continuation term (identical on every entry) is never a removable row (c32).
  const sharedCondition = conditionShared ? conditionRows(rules[0].condition, predecessorTerms(rules[0].condition)) : [];
  return (
    <fieldset aria-label="Shared by every entry point" className="fold-shared plain-group">
      <button ref={placeButton} type="button" className="fold-shared__placement" aria-expanded={placing} onClick={() => setPlacing((p) => !p)}>
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

export interface EntryEditorsProps {
  workflow: WorkflowDef;
  hrefFor?: (workflowId: string) => string;
  /** The entry point to open (`?entry=<rule id>`); a later change opens that one, without a remount. */
  entry?: string | null;
  /**
   * Live changes from the page's own stream (it must carry rules, runs, asks and rule_decisions).
   * Given, the editors open no EventSource of their own; absent, they open one.
   */
  live?: LiveFeed;
  /** The workflow was deleted from here (after its last entry point went). */
  onWorkflowDeleted?: (workflowId: string) => void;
}

/**
 * The entry editors of one workflow. Keyed by the workflow id inside, so another workflow starts
 * fresh (its first entry open, no earlier save results or baselines).
 */
export function EntryEditors(props: Readonly<EntryEditorsProps>) {
  return <WorkflowEntryEditors key={props.workflow.id} {...props} />;
}

function WorkflowEntryEditors({ workflow, hrefFor = defaultHref, entry, live: feed, onWorkflowDeleted }: Readonly<EntryEditorsProps>) {
  const workflowId = workflow.id;
  // undefined: not chosen yet (the asked-for entry, else the first, once the rules are in).
  const [open, setOpen] = useState<string | null | undefined>(undefined);
  const data = useRulesData(open ?? entry ?? undefined);
  const { refreshRules, refreshAsks } = data;
  const [historyTick, setHistoryTick] = useState(0);
  const onLive = useCallback(
    (changes: LiveChange[]) => {
      const touched = new Set(changes.map((c) => c.collection));
      if (touched.has("rules")) refreshRules();
      if (touched.has("runs") || touched.has("asks")) refreshAsks();
      if (touched.has("runs") || touched.has("rule_decisions")) setHistoryTick((n) => n + 1);
    },
    [refreshRules, refreshAsks],
  );
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
  const placement = splits.get("placement")!;
  const conditionShared =
    rules.length > 1 && rules.every((r) => r.condition && canonical(r.condition) === canonical(rules[0].condition));
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
    const name = workflow.name ?? workflowId;
    const result = await deleteUnusedWorkflow(workflowId);
    if (result.status === "deleted" || result.status === "in-use") setDeleted(null);
    setWorkflowNote(workflowDeleteNote(result, name, workflowId));
    if (result.status === "deleted") onWorkflowDeleted?.(workflowId);
    if (result.status === "in-use") refreshRules();
  };
  // Deleting the last entry point of a stored stepless (D7) workflow offers to delete it too (c30).
  const offerWorkflowDelete =
    Boolean(deleted?.last) &&
    entries.length === 0 &&
    (workflow.steps ?? []).length === 0 &&
    data.workflows.some((w) => w.id === workflowId);

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

  return (
    <section className="wf-entry-editors fold-simple" aria-label="Entry points and Then" data-live-flash={live.flash || undefined}>
      <h2 className="wf-panel__title">Entry points and Then</h2>
      {data.loadErrors.length > 0 ? (
        // a status (<output>), not alert: the page already raises its own alert for a failed load
        <output className="notice notice--error">{data.loadErrors.join(" · ")}</output>
      ) : null}
      {data.notice ? (
        // a refused write is announced once, here, as an alert (as the Simple view did)
        <p className="notice notice--error" role="alert">
          {data.notice}
        </p>
      ) : null}
      {data.stopOffer ? (
        <StopRunsNotice offer={data.stopOffer} onApprove={() => void data.stopRuns()} onDismiss={data.dismissStop} />
      ) : null}
      {deleted ? (
        <DeletedNotice
          name={deleted.rule.name}
          workflowName={workflow.name ?? workflowId}
          offerWorkflowDelete={offerWorkflowDelete}
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

      <div className="fold-board">
        <section className="fold-col fold-col--when" aria-label="Entry points">
          <div className="fold-col__head">
            <h3 className="fold-col__title">When</h3>
            <button ref={createButton} type="button" className="fold-add fold-add--big" aria-expanded={creating} onClick={() => setCreating((c) => !c)}>
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
          {data.loaded && entries.length === 0 ? <p className="fold-col__empty">No rule starts this workflow yet.</p> : null}
        </section>

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

export default EntryEditors;
