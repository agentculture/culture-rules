import { Fragment, useState } from "react";
import type { Rule, Workflow } from "../../api/types";
import { AboutButton } from "../../components/AboutButton";
import { Switch } from "../../culture-design/stages";
import { predecessorTerms, type FoldEntry } from "../../fold/model";
import { AddStageForm, AsksPanel, RuleEditForm } from "../../rules/Forms";
import { RelationCard, RelationSlots } from "../../rules/Relationships";
import { relationsOf } from "../../rules/relations";
import type { useRulesData } from "../../rules/useRulesData";
import { EntryHistory } from "./EntryHistory";
import { relationEdits } from "./relationEdits";
import { conditionRows, fieldOf, placementWords, triggerParts, type ConditionRow } from "./text";

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
  /** The workflow's attempt budget, for "counts toward the 3 attempts". */
  attempts: unknown;
  busy: boolean;
  historyTick: number;
  onCounts: (rule: Rule) => void;
  onPredecessor: (rule: Rule, workflowId: string) => void;
  onDelete: (rule: Rule) => void;
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

/** "Only if all of": one row per all-term, its operands in mono, a `vars.` operand as a teal chip. */
export function ConditionRows({ rows, label = "Only if all of" }: Readonly<{ rows: ConditionRow[]; label?: string }>) {
  if (rows.length === 0) return null;
  return (
    <>
      <span className="fold-entry__label" aria-hidden="true">{label}</span>
      <ul className="fold-conditions" aria-label={label}>
        {rows.map((row, i) => (
          // Rows have no identity of their own; their order is the stored order.
          <li key={i} className="fold-condition">
            {row.not ? <span className="fold-condition__not">not</span> : null}
            <span className={kindClass(row.leftKind)}>{row.left}</span>
            {row.op ? <span className="fold-condition__op">{row.op}</span> : null}
            {row.right ? <span className={kindClass(row.rightKind)}>{row.right}</span> : null}
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
  const source = (from: (typeof inputs)[number][1]) =>
    typeof from === "string" ? from : "$ref" in from ? from.$ref : JSON.stringify(from.$literal);
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

/** The expanded entry's body: everything the Rules tab showed and edited for this rule. */
function EntryBody({ entry, data, conditionShared, overrides, attempts, busy, historyTick, onCounts, onPredecessor, onDelete }: Readonly<EntryCardProps>) {
  const rule = entry.rule;
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [history, setHistory] = useState(false);
  const { rules } = data;
  const { relate, unrelate, moveRelation } = relationEdits(data);
  const { outgoing, incoming } = relationsOf(rules, rule.id);
  // The rule's run order (must / may run after, supersedes) opens by itself when it has any.
  const [ordering, setOrdering] = useState(outgoing.length + incoming.length > 0);
  const nameOf = (id: string) => rules.find((r) => r.id === id)?.name ?? id;
  const trigger = triggerParts(rule.trigger);
  const skip = entry.kind === "continuation" ? predecessorTerms(rule.condition) : [];
  const rows = conditionShared ? [] : conditionRows(rule.condition, skip);
  const counts = fieldOf(rule, "counts_toward_budget") !== false;
  const budget = typeof attempts === "number" ? `the ${attempts} attempts` : "the attempt budget";

  return (
    <div className="fold-entry__body">
      <div className="fold-entry__trigger">
        {trigger.kind}
        {trigger.value ? <span className="fold-term fold-term--field">{trigger.value}</span> : null}
      </div>
      {entry.kind === "continuation" ? (
        <Predecessor entry={entry} workflows={data.workflows} busy={busy} onChange={(id) => onPredecessor(rule, id)} />
      ) : null}
      <ConditionRows rows={rows} />
      {conditionShared ? <p className="fold-entry__meta">Its condition is shared by every entry point.</p> : null}

      {!rule.condition && !adding ? (
        <button type="button" className="fold-add" aria-label="Add condition" onClick={() => setAdding(true)}>
          <span aria-hidden="true">+</span> condition
        </button>
      ) : null}
      {adding ? (
        <AddStageForm rule={rule} workflows={data.workflows} choice="condition" onSave={data.save} onCancel={() => setAdding(false)} />
      ) : null}

      {overrides.length > 0 ? (
        <ul className="fold-overrides" aria-label="Overrides">
          {overrides.map((o) => (
            <li key={o.field}>
              <span className="fold-badge fold-badge--override">override</span> {o.text}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="fold-entry__foot">
        <InputsBound rule={rule} />
        <span className="fold-entry__budget">
          <Switch label="Counts toward the attempt budget" checked={counts} disabled={busy} onChange={() => onCounts(rule)} />
          {counts ? `counts toward ${budget}` : `does not count toward ${budget}`}
        </span>
      </div>

      <button
        type="button"
        className="fold-chip-button fold-entry__order-toggle"
        aria-expanded={ordering}
        onClick={() => setOrdering((o) => !o)}
      >
        Run order{outgoing.length + incoming.length > 0 ? ` (${outgoing.length + incoming.length})` : ""} {ordering ? "▾" : "▸"}
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

      <div className="fold-entry__tools">
        <button type="button" className="btn" aria-label={`Edit ${rule.name}`} aria-pressed={editing} onClick={() => setEditing((e) => !e)}>
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
          key={rule.id}
          rule={rule}
          machines={data.machines}
          workflows={data.workflows}
          actors={data.actors}
          onSave={data.save}
          onCancel={() => setEditing(false)}
        />
      ) : null}
      {history ? <EntryHistory ruleId={rule.id} nameOf={nameOf} tick={historyTick} /> : null}
      {data.asks && data.selected?.id === rule.id ? <AsksPanel asks={data.asks.items} onAnswer={data.answer} /> : null}
    </div>
  );
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
    <div
      role="group"
      aria-label={`Entry point: ${rule.name}`}
      className={`fold-entry${expanded ? " is-open" : ""}${entry.enabled ? "" : " is-disabled"}`}
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
    </div>
  );
}

/** "evaluates on spark2" — the words the shared placement button and overrides use. */
export const evaluates = (rule: Rule) => `evaluates ${placementWords(rule.placement)}`;
