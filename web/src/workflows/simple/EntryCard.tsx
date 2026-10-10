import { Fragment, useState } from "react";
import { ApiError } from "../../api/client";
import type { Action, Rule, Workflow } from "../../api/types";
import { AboutButton } from "../../components/AboutButton";
import { Switch } from "../../culture-design/stages";
import { predecessorTerms, type FoldEntry } from "../../fold/model";
import { AddStageForm, AsksPanel, RuleEditForm } from "../../rules/Forms";
import { RelationCard, RelationSlots } from "../../rules/Relationships";
import { relationsOf } from "../../rules/relations";
import type { useRulesData } from "../../rules/useRulesData";
import { EntryHistory } from "./EntryHistory";
import { relationEdits } from "./relationEdits";
import { useFocusReturn } from "./focus";
import { useFrozen } from "./freeze";
import { getRule } from "../../api/rules";
import { GroupForm, groupProblems, RunsForm, SharedActionForm, type GroupEdit, type GroupProblems } from "./SharedForms";
import type { FoldWriteResult } from "./useFanout";
import { canonical, conditionRows, countsEdit, fieldOf, groupWords, placementWords, rowText, triggerParts, withTerm, withoutTerm, type ConditionRow, type RunsEdit } from "./text";

/** The stored fields that differ between two versions of a rule (server bookkeeping aside). */
function changedFields(before: Rule, after: Rule): string[] {
  const a = before as unknown as Record<string, unknown>;
  const b = after as unknown as Record<string, unknown>;
  return [...new Set([...Object.keys(a), ...Object.keys(b)])]
    .filter((field) => field !== "updated_at" && canonical(a[field]) !== canonical(b[field]));
}

export type RulesData = ReturnType<typeof useRulesData>;

export interface Override {
  field: string;
  text: string;
}

export interface EntryCardProps {
  entry: FoldEntry;
  expanded: boolean;
  onExpand: (open: boolean) => void;
  data: RulesData;
  /** The condition is identical on every entry point and shown once for the workflow. */
  conditionShared: boolean;
  /** This entry's values that differ from the workflow's (D3-D6), in words. */
  overrides: Override[];
  busy: boolean;
  historyTick: number;
  onCounts: (rule: Rule) => void;
  onPredecessor: (rule: Rule, workflowId: string) => void;
  onDelete: (rule: Rule) => void;
  /** Write one field set to this entry's rule alone (an override), through the fold writes. */
  onOverride: (rule: Rule, label: string, edit: Record<string, unknown>) => Promise<FoldWriteResult[]>;
}

const BoltIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
    <path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />
  </svg>
);
const LoopIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M9 14 4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3" />
  </svg>
);
const Chevron = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M6 9l6 6 6-6" />
  </svg>
);

const kindClass = (kind: ConditionRow["leftKind"]) => `fold-term fold-term--${kind}`;

/**
 * A key per row from what it says: rows have no identity of their own, so a row repeated word
 * for word is told apart by how many times those words came before it.
 */
function keyedRows(rows: readonly ConditionRow[]): { row: ConditionRow; key: string }[] {
  const seen = new Map<string, number>();
  return rows.map((row) => {
    const text = `${row.leftKind}:${row.rightKind}:${rowText(row)}`;
    const nth = seen.get(text) ?? 0;
    seen.set(text, nth + 1);
    return { row, key: nth === 0 ? text : `${text}#${nth}` };
  });
}

/** "Only if all of": one row per all-term, its operands in mono, a `vars.` operand as a teal chip. */
export function ConditionRows({
  rows,
  label = "Only if all of",
  busy = false,
  onRemove,
}: Readonly<{ rows: ConditionRow[]; label?: string; busy?: boolean; onRemove?: (row: ConditionRow) => void }>) {
  if (rows.length === 0) return null;
  return (
    <>
      <span className="fold-entry__label" aria-hidden="true">{label}</span>
      <ul className="fold-conditions" aria-label={label}>
        {keyedRows(rows).map(({ row, key }) => (
          // Their order is the stored order.
          <li key={key} className="fold-condition">
            {row.not ? <span className="fold-condition__not">not</span> : null}
            <span className={kindClass(row.leftKind)}>{row.left}</span>
            {row.op ? <span className="fold-condition__op">{row.op}</span> : null}
            {row.right ? <span className={kindClass(row.rightKind)}>{row.right}</span> : null}
            {onRemove && row.node ? (
              <button
                type="button"
                className="relationship__remove fold-condition__remove"
                aria-label={`Remove condition ${rowText(row)}`}
                disabled={busy}
                onClick={() => onRemove(row)}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" aria-hidden="true">
                  <path d="M6 6l12 12M18 6 6 18" />
                </svg>
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </>
  );
}

/** A continuation's predecessor: a picker of the other workflows, or words when it cannot be edited. */
function Predecessor({
  entry,
  workflows,
  busy,
  onChange,
}: Readonly<{ entry: Extract<FoldEntry, { kind: "continuation" }>; workflows: Workflow[]; busy: boolean; onChange: (id: string) => void }>) {
  if (entry.predecessor.kind !== "linked") {
    return <p className="fold-entry__from">Continues {entry.fromLabel}</p>;
  }
  const current = entry.predecessor.workflowId;
  // Never the workflow it starts, never an empty id (savePredecessor would accept both).
  const options = workflows.filter((w) => w.id && w.id !== entry.workflowId);
  const missing = !options.some((w) => w.id === current);
  return (
    <label className="fold-entry__from">
      <span aria-hidden="true">Continues from</span>
      <select aria-label="Continues from" value={current} disabled={busy} onChange={(e) => e.target.value && e.target.value !== current && onChange(e.target.value)}>
        {missing ? <option value={current}>{current} (missing)</option> : null}
        {options.map((w) => (
          <option key={w.id} value={w.id}>
            {w.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function InputsBound({ rule }: Readonly<{ rule: Rule }>) {
  const [open, setOpen] = useState(false);
  const inputs = Object.entries(rule.workflow?.inputs ?? {});
  if (inputs.length === 0) return <span className="fold-entry__meta">no inputs bound</span>;
  const source = (from: (typeof inputs)[number][1]): string => {
    if (typeof from === "string") return from;
    return "$ref" in from ? from.$ref : JSON.stringify(from.$literal);
  };
  return (
    <span className="fold-entry__inputs">
      <button type="button" className="fold-chip-button" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {inputs.length} input{inputs.length === 1 ? "" : "s"} bound {open ? "▾" : "▸"}
      </button>
      {open ? (
        <ul className="fold-entry__bindings" aria-label="Inputs bound">
          {inputs.map(([name, from]) => (
            <li key={name}>
              <span className="fold-term fold-term--field">{name}</span> ← <span className="fold-term fold-term--field">{source(from)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </span>
  );
}

const errorMessage = (err: unknown) => (err instanceof Error ? err.message : String(err));

type Conflict = { current: Rule; changed: string[]; form: boolean };

/** This entry's overrides of the workflow's values (D3-D6), in words. */
function OverrideList({ overrides }: Readonly<{ overrides: Override[] }>) {
  if (overrides.length === 0) return null;
  return (
    <ul className="fold-overrides" aria-label="Overrides">
      {overrides.map((o) => (
        <li key={o.field}>
          <span className="fold-badge fold-badge--override">override</span> {o.text}
        </li>
      ))}
    </ul>
  );
}

/** Whether the rule counts toward the attempt budget: its switch, and the budget in words. */
function BudgetFoot({ rule, busy, onCounts }: Readonly<{ rule: Rule; busy: boolean; onCounts: (rule: Rule) => void }>) {
  const counts = fieldOf(rule, "counts_toward_budget") !== false;
  const own = fieldOf(rule, "max_attempts");
  const budget = typeof own === "number" ? `the ${own} attempts` : "the attempt budget";
  const canSwitch = countsEdit(rule) !== null;
  return (
    <div className="fold-entry__foot">
      <InputsBound rule={rule} />
      <span className="fold-entry__budget">
        <Switch label="Counts toward the attempt budget" checked={counts} disabled={busy || !canSwitch} onChange={() => onCounts(rule)} />
        {counts ? `counts toward ${budget}` : `does not count toward ${budget}`}
        {canSwitch ? null : <span className="fold-entry__meta"> (opting out needs a run key)</span>}
      </span>
    </div>
  );
}

type Overriding = "failure" | "runs" | "group" | null;

/**
 * "Only for this entry point": the on-failure and run-key overrides, and the rule's exclusive
 * group and priority (issue #29), each with its form.
 */
function OverrideTools({
  entry,
  data,
  busy,
  onOverride,
}: Readonly<Pick<EntryCardProps, "entry" | "data" | "busy" | "onOverride">>) {
  const rule = entry.rule;
  const [overriding, setOverriding] = useState<Overriding>(null);
  const failureButton = useFocusReturn<HTMLButtonElement>(overriding === "failure");
  const runsButton = useFocusReturn<HTMLButtonElement>(overriding === "runs");
  const groupButton = useFocusReturn<HTMLButtonElement>(overriding === "group");
  const trigger = triggerParts(rule.trigger);
  const workflow = data.workflows.find((w) => w.id === entry.workflowId);
  const type = rule.trigger.kind === "event" ? trigger.value || undefined : undefined;
  // The override forms keep the rule as it was when they opened (c27).
  const at = useFrozen(overriding, rule);
  const override = (label: string, edit: Record<string, unknown>) => {
    setOverriding(null);
    void onOverride(at, `${rule.name}: ${label}`, edit);
  };
  // The group form stays open on a 422, with the server's words at the field it names; any
  // other outcome closes it and the save results say what happened.
  const saveGroup = async (edit: GroupEdit): Promise<GroupProblems | null> => {
    const [result] = await onOverride(at, `${rule.name}: group and priority`, { ...edit });
    const refused = result?.status === "failed" && result.phase === "write" ? result.error : null;
    if (refused instanceof ApiError && refused.status === 422) return groupProblems(refused, edit);
    setOverriding(null);
    return null;
  };
  const toggle = (which: Exclude<Overriding, null>) => () => setOverriding((o) => (o === which ? null : which));
  return (
    <>
      <div className="fold-entry__tools">
        <span className="fold-entry__label">Only for this entry point:</span>
        <button
          ref={failureButton}
          type="button"
          className="fold-chip-button"
          aria-expanded={overriding === "failure"}
          aria-label={`On failure for ${rule.name}`}
          onClick={toggle("failure")}
        >
          On failure
        </button>
        <button
          ref={runsButton}
          type="button"
          className="fold-chip-button"
          aria-expanded={overriding === "runs"}
          aria-label={`Runs for ${rule.name}`}
          onClick={toggle("runs")}
        >
          Run key and budget
        </button>
        <button
          ref={groupButton}
          type="button"
          className="fold-chip-button"
          aria-expanded={overriding === "group"}
          aria-label={`Group and priority for ${rule.name}`}
          onClick={toggle("group")}
        >
          {groupWords(rule)}
        </button>
      </div>
      {overriding === "failure" ? (
        <SharedActionForm
          label={`On failure for ${rule.name}`}
          scope="this entry point"
          value={at.on_failure}
          actors={data.actors}
          triggerType={type}
          workflow={workflow}
          busy={busy}
          onSave={(action: Action) => override("on failure", { on_failure: action })}
          onRemove={at.on_failure ? () => override("on failure", { on_failure: null }) : undefined}
          onCancel={() => setOverriding(null)}
        />
      ) : null}
      {overriding === "runs" ? (
        <RunsForm
          label={`Runs for ${rule.name}`}
          scope="this entry point"
          rules={[at]}
          runKey={fieldOf(at, "concurrency_key")}
          attempts={fieldOf(at, "max_attempts")}
          busy={busy}
          onSave={(edit: RunsEdit) => override("runs", { ...edit })}
          onCancel={() => setOverriding(null)}
        />
      ) : null}
      {overriding === "group" ? (
        <GroupForm
          label={`Group and priority for ${rule.name}`}
          rule={at}
          rules={data.rules}
          busy={busy}
          onSave={saveGroup}
          onCancel={() => setOverriding(null)}
        />
      ) : null}
    </>
  );
}

/** The rule's run order (must / may run after, supersedes); it opens by itself when it has any. */
function RunOrder({
  rule,
  rules,
  edits,
}: Readonly<{ rule: Rule; rules: Rule[]; edits: ReturnType<typeof relationEdits> }>) {
  const { relate, unrelate, moveRelation } = edits;
  const { outgoing, incoming } = relationsOf(rules, rule.id);
  const total = outgoing.length + incoming.length;
  const [ordering, setOrdering] = useState(total > 0);
  const nameOf = (id: string) => rules.find((r) => r.id === id)?.name ?? id;
  return (
    <>
      <button
        type="button"
        className="fold-chip-button fold-entry__order-toggle"
        aria-expanded={ordering}
        onClick={() => setOrdering((o) => !o)}
      >
        Run order{total > 0 ? ` (${total})` : ""} {ordering ? "▾" : "▸"}
      </button>
      {ordering ? (
        <fieldset className="fold-entry__order plain-group" aria-label={`Order of ${rule.name}`}>
          <RelationSlots rules={rules} focused={rule} dragging={null} onAdd={(kind, id) => relate(rule, kind, id)} onMove={moveRelation} />
          {outgoing.map((rel) => (
            <Fragment key={`${rel.kind}-${rel.to}`}>
              <RelationCard rel={rel} direction="out" nameOf={nameOf} onRemove={unrelate} />
            </Fragment>
          ))}
          {incoming.map((rel) => (
            <RelationCard key={`${rel.kind}-${rel.from}`} rel={rel} direction="in" nameOf={nameOf} onRemove={unrelate} />
          ))}
        </fieldset>
      ) : null}
    </>
  );
}

/** A direct write found the rule changed since it was shown: say so, and offer to reload. */
function ConflictNotice({
  rule,
  conflict,
  editing,
  onReload,
  onShow,
}: Readonly<{ rule: Rule; conflict: Conflict; editing: boolean; onReload: () => void; onShow: () => void }>) {
  return (
    <div className="notice notice--error fold-conflict" role="alert">
      <span>
        {rule.name} changed since you opened it, so this was not saved
        {conflict.changed.length ? ` (changed: ${conflict.changed.join(", ")})` : ""}.
      </span>
      {conflict.form && editing ? (
        <button type="button" className="btn btn--primary" onClick={onReload}>
          Reload the form from the stored rule
        </button>
      ) : (
        <button type="button" className="btn" onClick={onShow}>
          Show the stored rule
        </button>
      )}
    </div>
  );
}

/** The expanded entry's body: everything the Rules tab showed and edited for this rule. */
function EntryBody({ entry, data, conditionShared, overrides, busy, historyTick, onCounts, onPredecessor, onDelete, onOverride }: Readonly<EntryCardProps>) {
  const rule = entry.rule;
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [history, setHistory] = useState(false);
  const editButton = useFocusReturn<HTMLButtonElement>(editing);
  const addButton = useFocusReturn<HTMLButtonElement>(adding);
  const { rules } = data;
  // c27 for this entry's direct writes (the Rules tab's forms and calls, d3): re-read the rule
  // first; if it changed since it was shown (or since the form opened), nothing is written.
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [editBase, setEditBase] = useState<Rule | null>(null);
  const [editKey, setEditKey] = useState(0);
  const guarded = async (snapshot: Rule, next: Rule, form = false): Promise<boolean> => {
    let current: Rule;
    try {
      current = await getRule(snapshot.id);
    } catch (err) {
      data.setNotice(`${snapshot.name} could not be re-read, so nothing was saved: ${errorMessage(err)}`);
      return false;
    }
    if (canonical(current) !== canonical(snapshot)) {
      setConflict({ current, changed: changedFields(snapshot, current), form });
      return false;
    }
    setConflict(null);
    return data.save(next);
  };
  const edits = relationEdits({
    rules: data.rules,
    setNotice: data.setNotice,
    save: (next) => guarded(data.rules.find((r) => r.id === next.id) ?? next, next),
  });
  const nameOf = (id: string) => rules.find((r) => r.id === id)?.name ?? id;
  const trigger = triggerParts(rule.trigger);
  // A continuation's predecessor term has its own control: never a row, never removed here (c32).
  const skip = entry.kind === "continuation" ? predecessorTerms(rule.condition) : [];
  const rows = conditionShared ? [] : conditionRows(rule.condition, skip);
  const addingAt = useFrozen(adding ? "add-condition" : null, rule);

  return (
    <div className="fold-entry__body">
      <div className="fold-entry__trigger">
        {trigger.kind}
        {trigger.value ? <span className="fold-term fold-term--field">{trigger.value}</span> : null}
      </div>
      {entry.kind === "continuation" ? (
        <Predecessor entry={entry} workflows={data.workflows} busy={busy} onChange={(id) => onPredecessor(rule, id)} />
      ) : null}
      <ConditionRows
        rows={rows}
        onRemove={(row) => {
          // The row's button goes with it: focus moves on to "+ condition".
          addButton.current?.focus();
          void guarded(rule, { ...rule, condition: withoutTerm(rule.condition, row.node!) });
        }}
      />
      {conditionShared ? <p className="fold-entry__meta">Its condition is shared by every entry point.</p> : null}

      {conditionShared ? null : (
        <button
          ref={addButton}
          type="button"
          className="fold-add"
          aria-label="Add condition"
          aria-expanded={adding}
          onClick={() => setAdding((a) => !a)}
        >
          <span aria-hidden="true">+</span> condition
        </button>
      )}
      {adding ? (
        // The Rules tab's condition form; its one new term joins this entry's own (and keeps the rest).
        <AddStageForm
          rule={addingAt}
          workflows={data.workflows}
          choice="condition"
          onSave={async (next) => {
            // Built from, and guarded against, the rule as it was when the form opened (c27).
            const condition = withTerm(addingAt.condition, next.condition!);
            // Already one of its terms: nothing to write.
            if (condition === addingAt.condition) return true;
            return guarded(addingAt, { ...addingAt, condition });
          }}
          onCancel={() => setAdding(false)}
        />
      ) : null}

      <OverrideList overrides={overrides} />

      <BudgetFoot rule={rule} busy={busy} onCounts={onCounts} />

      <OverrideTools entry={entry} data={data} busy={busy} onOverride={onOverride} />

      <RunOrder rule={rule} rules={rules} edits={edits} />

      <div className="fold-entry__tools">
        <button
          ref={editButton}
          type="button"
          className="btn"
          aria-label={`Edit ${rule.name}`}
          aria-pressed={editing}
          onClick={() => {
            // The form edits the rule as it is now, and its save compares against that (c27).
            if (!editing) setEditBase(rule);
            setConflict(null);
            setEditing((e) => !e);
          }}
        >
          Edit
        </button>
        <AboutButton noun="rules" id={rule.id} name={rule.name} />
        <button type="button" className="btn" aria-label={`History of ${rule.name}`} aria-expanded={history} onClick={() => setHistory((h) => !h)}>
          History
        </button>
        <span className="fold-spacer" />
        <button type="button" className="btn fold-danger" aria-label={`Delete ${rule.name}`} onClick={() => onDelete(rule)}>
          Delete
        </button>
      </div>
      {editing ? (
        <RuleEditForm
          key={`${rule.id}-${editKey}`}
          rule={editBase ?? rule}
          machines={data.machines}
          workflows={data.workflows}
          actors={data.actors}
          onSave={(next) => guarded(editBase ?? rule, next, true)}
          onCancel={() => {
            setEditing(false);
            setConflict(null);
          }}
        />
      ) : null}
      {conflict ? (
        <ConflictNotice
          rule={rule}
          conflict={conflict}
          editing={editing}
          onReload={() => {
            setEditBase(conflict.current);
            setEditKey((k) => k + 1);
            setConflict(null);
            data.refreshRules();
          }}
          onShow={() => {
            setConflict(null);
            data.refreshRules();
          }}
        />
      ) : null}
      {history ? <EntryHistory ruleId={rule.id} nameOf={nameOf} tick={historyTick} /> : null}
      {data.asks && data.selected?.id === rule.id ? <AsksPanel asks={data.asks.items} onAnswer={data.answer} /> : null}
    </div>
  );
}

function entryClass(expanded: boolean, enabled: boolean): string {
  const classes = ["fold-entry", "plain-group"];
  if (expanded) classes.push("is-open");
  if (!enabled) classes.push("is-disabled");
  return classes.join(" ");
}

/**
 * One When entry point (canvas Fold-Editor): the rule that starts this
 * workflow, with its own enable switch. Collapsed it is one row (name,
 * trigger or predecessor, badges); expanded it shows and edits every field
 * the Rules tab did — through the Rules tab's own forms and calls, so a save
 * is the same rule document.
 */
export function EntryCard(props: Readonly<EntryCardProps>) {
  const { entry, expanded, onExpand, data, overrides } = props;
  const rule = entry.rule;
  const continuation = entry.kind === "continuation";
  const sub = continuation ? entry.fromLabel : triggerParts(rule.trigger).value || rule.trigger.kind;
  return (
    <fieldset
      aria-label={`Entry point: ${rule.name}`}
      className={entryClass(expanded, entry.enabled)}
      data-rule-id={rule.id}
      data-enabled={String(entry.enabled)}
    >
      <div className="fold-entry__head">
        <Switch
          label={`${rule.name} enabled`}
          checked={entry.enabled}
          disabled={data.togglePending?.has(rule.id)}
          onChange={() => data.toggle(rule)}
        />
        <span className="fold-entry__icon">{continuation ? <LoopIcon /> : <BoltIcon />}</span>
        <span className="fold-entry__title">
          <span className="fold-entry__name-line">
            <span className="fold-entry__name">{rule.name}</span>
            {continuation ? <span className="fold-badge">continuation</span> : null}
            {entry.enabled ? null : <span className="fold-badge fold-badge--off">disabled</span>}
            {!expanded && overrides.length > 0 ? <span className="fold-badge fold-badge--override">override</span> : null}
          </span>
          {expanded ? null : <span className="fold-entry__sub">{sub}</span>}
        </span>
        <button
          type="button"
          className={`fold-entry__expand${expanded ? " is-open" : ""}`}
          aria-label={`${expanded ? "Collapse" : "Expand"} ${rule.name}`}
          aria-expanded={expanded}
          onClick={() => onExpand(!expanded)}
        >
          <Chevron />
        </button>
      </div>
      {expanded ? <EntryBody {...props} /> : null}
    </fieldset>
  );
}

/** "evaluates on spark2" — the words the shared placement button and overrides use. */
export const evaluates = (rule: Rule) => `evaluates ${placementWords(rule.placement)}`;
