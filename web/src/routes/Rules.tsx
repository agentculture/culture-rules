import { Fragment, useCallback, useEffect, useMemo, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { useLiveUpdates, type LiveChange } from "../api/live";
import { getRuleHistory, type RuleDoc, type RuleHistoryItem } from "../api/rules";
import type { Condition } from "../api/types";
import { setAgentState } from "../agent-state/store";
import { machineColors } from "../culture-design/chart";
import { AddStageButton, Stage, StageArrow, machineStyle, type StageKind } from "../culture-design/stages";
import { AddStageForm, AsksPanel, NewRuleForm, RuleEditForm, type StageChoice } from "../rules/Forms";
import { RuleList } from "../rules/RuleList";
import { RelationCard, RelationSlots } from "../rules/Relationships";
import {
  canRelate,
  relationsOf,
  withRelation,
  withoutRelation,
  type Relation,
  type RelationKind,
} from "../rules/relations";
import { useRulesData } from "../rules/useRulesData";
import "../rules/rules.css";
import { useTabReady } from "./useTabReady";
import {
  actionChips,
  ago,
  conditionText,
  stagesOf,
  triggerLabel,
  upstreamVars,
  workflowChips,
} from "./rules-view";

const LIVE_COLLECTIONS = ["rules", "runs", "asks", "rule_decisions"] as const;
const HISTORY_LIMIT = 6;

const SKIP_WORDS: Record<string, string> = {
  superseded_by: "superseded by",
  group_lost: "lost its group to",
  blocked_by_predecessor: "waiting for",
};

function SkipIcon() {
  return (
    <svg className="last-runs__skip-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 5l7 7-7 7M13 5l7 7-7 7" />
    </svg>
  );
}

/** Where the rule runs, as the placement chip words it. */
function placementText(rule: RuleDoc): string {
  if (rule.placement?.machine) return `on ${rule.placement.machine}`;
  if (rule.placement?.actor) return `via ${rule.placement.actor}`;
  return "anywhere";
}

/** The condition stage's words: its operand in mono, then the rest. */
function ConditionLabel({ condition }: Readonly<{ condition: Condition }>) {
  const text = conditionText(condition);
  return (
    <>
      {text.subject ? <span className="mono-var">{text.subject}</span> : null}
      {text.subject ? " " : null}
      {text.rest}
    </>
  );
}

/** The focused rule's vertical stage flow (trigger → condition → workflow → action). */
function RuleStages({
  rule,
  stages,
  workflowName,
}: Readonly<{ rule: RuleDoc; stages: StageKind[]; workflowName: (id: string) => string }>) {
  return (
    <ol className="stages" aria-label="Stages">
      {stages.map((kind, i) => (
        <li key={kind} className="stages__item">
          {i > 0 ? <StageArrow /> : null}
          {kind === "trigger" ? <Stage kind="trigger" label={triggerLabel(rule)} /> : null}
          {kind === "condition" && rule.condition ? (
            <Stage kind="condition" label={<ConditionLabel condition={rule.condition} />} />
          ) : null}
          {kind === "workflow" && rule.workflow ? (
            <Stage kind="workflow" label={workflowName(rule.workflow.id)} chips={workflowChips(rule.workflow)} />
          ) : null}
          {kind === "action" ? (
            <Stage kind="action" label={rule.action.name || rule.action.kind} chips={actionChips(rule.action)} />
          ) : null}
        </li>
      ))}
    </ol>
  );
}

type DecisionItem = Extract<RuleHistoryItem, { kind: "decision" }>;

/** A recorded skip, in words: "superseded by X", or the engine's message. */
function skipText(item: DecisionItem, nameOf: (id: string) => string): string {
  const words = SKIP_WORDS[item.reason];
  if (words) return `${words} ${item.by.map(nameOf).join(", ")}`;
  return item.message ?? item.reason;
}

/** The right-hand 'Last runs' column: runs and recorded skips, newest first. */
function LastRuns({
  items,
  now,
  nameOf,
}: Readonly<{ items: RuleHistoryItem[]; now: number; nameOf: (id: string) => string }>) {
  return (
    <aside className="last-runs" aria-label="Last runs">
      <span className="last-runs__title">Last runs</span>
      <ul className="last-runs__list">
        {items.map((item) =>
          item.kind === "decision" ? (
            <li
              key={`d-${item.event_id}`}
              className="last-runs__run last-runs__skip"
              data-decision={item.reason}
            >
              <SkipIcon />
              <span className="last-runs__skip-text">
                <span className="last-runs__skip-label">skipped · {ago(item.at, now)}</span>
                <span>{skipText(item, nameOf)}</span>
              </span>
            </li>
          ) : (
            <li key={item.id} className="last-runs__run" data-run-status={item.status}>
              <span className={`run-dot run-dot--${item.status}`} aria-hidden="true" />
              <span className="sr-only">{item.status}, </span>
              {ago(item.created_at, now)}
            </li>
          ),
        )}
      </ul>
    </aside>
  );
}

type RulesData = ReturnType<typeof useRulesData>;
type RuleHistory = { ruleId: string; items: RuleHistoryItem[]; error: string | null };

const errorText = (err: unknown) => (err instanceof ApiError ? err.message : String(err));

/** The rule's contextual history: runs plus recorded skips (GET /rules/{id}/history). */
function useRuleHistory(ruleId: string | undefined) {
  const [historyTick, setHistoryTick] = useState(0);
  const [runs, setRuns] = useState<RuleHistory | null>(null);
  useEffect(() => {
    if (!ruleId) return;
    const controller = new AbortController();
    getRuleHistory(ruleId, HISTORY_LIMIT, controller.signal)
      .then((items) => setRuns({ ruleId, items, error: null }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setRuns({ ruleId, items: [], error: errorText(err) });
      });
    return () => controller.abort();
  }, [ruleId, historyTick]);
  const refreshHistory = useCallback(() => setHistoryTick((n) => n + 1), []);
  // Settled once the focused rule's history (not a previous rule's) is in.
  const settled = !ruleId || runs?.ruleId === ruleId;
  return { runs: settled ? runs : null, settled, refreshHistory };
}

/** Live: another editor's write (or the engine's) refetches what it touches. */
function routeLiveChanges(
  changes: LiveChange[],
  refresh: { rules: () => void; asks: () => void; history: () => void },
) {
  const touched = new Set(changes.map((c) => c.collection));
  if (touched.has("rules")) refresh.rules();
  if (touched.has("runs") || touched.has("asks")) refresh.asks();
  if (touched.has("runs") || touched.has("rule_decisions")) refresh.history();
}

/** Delete the focused rule (focusing a neighbour), with an undo that restores it. */
function useRemoveWithUndo(data: RulesData) {
  const navigate = useNavigate();
  const { rules, selected } = data;
  const [deleted, setDeleted] = useState<RuleDoc | null>(null);
  const removeSelected = async () => {
    if (!selected) return;
    const index = rules.findIndex((r) => r.id === selected.id);
    const next = rules[index + 1] ?? rules[index - 1];
    if (!(await data.remove(selected))) return;
    setDeleted(selected);
    // `void`: under the declarative <BrowserRouter> navigate is synchronous (it only
    // returns a promise inside a data router), so there is no rejection to handle.
    void navigate(next ? `/rules/${encodeURIComponent(next.id)}` : "/rules", { replace: true });
  };
  const undo = async () => {
    if (!deleted) return;
    const doc = await data.restore(deleted);
    if (!doc) return;
    setDeleted(null);
    void navigate(`/rules/${encodeURIComponent(doc.id)}`); // synchronous here, see above
  };
  return { deleted, dismiss: () => setDeleted(null), removeSelected, undo };
}

/** Relationship edits: each one is a PUT of the rule that declares it. */
function useRelationEdits(data: RulesData) {
  const { rules } = data;
  const relate = async (from: RuleDoc, kind: RelationKind, target: string) => {
    const why = canRelate(rules, from.id, kind, target);
    if (why) return data.setNotice(`${from.name}: ${why}`);
    await data.save(withRelation(from, kind, target));
  };
  const unrelate = async (rel: Relation) => {
    const from = rules.find((r) => r.id === rel.from);
    if (from) await data.save(withoutRelation(from, rel.kind, rel.to));
  };
  const moveRelation = async (rel: Relation, to: RelationKind) => {
    const from = rules.find((r) => r.id === rel.from);
    if (!from || rel.kind === to) return;
    const rest = { ...from, [rel.kind]: (from[rel.kind] ?? []).filter((t) => t !== rel.to) };
    const why = canRelate([rest, ...rules.filter((r) => r.id !== from.id)], from.id, to, rel.to);
    if (why) return data.setNotice(`${from.name}: ${why}`);
    await data.save(withRelation(rest, to, rel.to));
  };
  return { relate, unrelate, moveRelation };
}

const PencilIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M4 20h4L19 9l-4-4L4 16v4z" />
  </svg>
);
const TrashIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
  </svg>
);

/** The `+` menu: which optional stage to add next. */
function StageMenu({
  rule,
  hasWorkflows,
  onPick,
}: Readonly<{ rule: RuleDoc; hasWorkflows: boolean; onPick: (choice: StageChoice) => void }>) {
  return (
    <fieldset className="stage-menu plain-group" aria-label="Add a stage">
      <button type="button" className="btn" disabled={!!rule.condition} onClick={() => onPick("condition")}>
        Add condition
      </button>
      <button type="button" className="btn" disabled={!!rule.workflow || !hasWorkflows} onClick={() => onPick("workflow")}>
        Add workflow
      </button>
    </fieldset>
  );
}

interface FocusedRuleProps {
  rule: RuleDoc;
  data: RulesData;
  stages: StageKind[];
  slot: number | null;
  editing: boolean;
  setEditing: Dispatch<SetStateAction<boolean>>;
  adding: StageChoice | null;
  setAdding: (choice: StageChoice | null) => void;
  menu: boolean;
  setMenu: Dispatch<SetStateAction<boolean>>;
  dragging: string | null;
  nameOf: (id: string) => string;
  onDelete: () => void;
}

/**
 * The focused rule drawn as a vertical stage flow: its head (name, placement,
 * edit, delete), pending asks, relationship slots and cards, the stages and
 * the `+` that grows it.
 */
function FocusedRule({
  rule,
  data,
  stages,
  slot,
  editing,
  setEditing,
  adding,
  setAdding,
  menu,
  setMenu,
  dragging,
  nameOf,
  onDelete,
}: Readonly<FocusedRuleProps>) {
  const { rules } = data;
  const { relate, unrelate, moveRelation } = useRelationEdits(data);
  const { outgoing, incoming } = relationsOf(rules, rule.id);
  const workflowName = (id: string) => data.workflows.find((w) => w.id === id)?.name ?? id;
  const outgoingChip = (rel: Relation, i: number) =>
    rel.kind === "must_after" && i === 0 ? upstreamVars(rule).join(", ") : undefined;
  return (
    <>
      <div className="rule-flow__head">
        <h1 className="rule-title">{rule.name}</h1>
        <button
          type="button"
          className="placement-chip"
          data-machine-slot={slot ?? "none"}
          style={machineStyle(slot)}
          onClick={() => setEditing(true)}
        >
          {placementText(rule)}{" "}
          <span aria-hidden="true">▾</span>
        </button>
        <button type="button" className="icon-button" aria-label="Edit rule" aria-pressed={editing} onClick={() => setEditing((e) => !e)}>
          <PencilIcon />
        </button>
        <button type="button" className="icon-button icon-button--danger" aria-label="Delete rule" onClick={onDelete}>
          <TrashIcon />
        </button>
      </div>

      {editing ? (
        <RuleEditForm key={rule.id} rule={rule} machines={data.machines} onSave={data.save} onCancel={() => setEditing(false)} />
      ) : null}

      <AsksPanel asks={data.asks?.items ?? []} onAnswer={data.answer} />

      <RelationSlots rules={rules} focused={rule} dragging={dragging} onAdd={(kind, id) => relate(rule, kind, id)} onMove={moveRelation} />

      {outgoing.map((rel, i) => (
        <Fragment key={`${rel.kind}-${rel.to}`}>
          <RelationCard rel={rel} direction="out" nameOf={nameOf} chip={outgoingChip(rel, i)} onRemove={unrelate} />
          <span className="relationship-link" aria-hidden="true" />
        </Fragment>
      ))}

      <RuleStages rule={rule} stages={stages} workflowName={workflowName} />

      {adding ? (
        <AddStageForm key={adding} rule={rule} workflows={data.workflows} choice={adding} onSave={data.save} onCancel={() => setAdding(null)} />
      ) : (
        <AddStageButton onClick={() => setMenu((m) => !m)} />
      )}
      {menu && !adding ? (
        <StageMenu
          rule={rule}
          hasWorkflows={data.workflows.length > 0}
          onPick={(choice) => {
            setAdding(choice);
            setMenu(false);
          }}
        />
      ) : null}

      {incoming.length > 0 ? (
        <div className="relationship-after">
          {incoming.map((rel) => (
            <RelationCard key={`${rel.kind}-${rel.from}`} rel={rel} direction="in" nameOf={nameOf} onRemove={unrelate} />
          ))}
        </div>
      ) : null}
    </>
  );
}

/** The machine palette slot a rule's placement colors with; null = neutral. */
function slotFor(slots: Map<string, number>, rule: RuleDoc): number | null {
  const machine = rule.placement?.machine;
  return machine ? (slots.get(machine) ?? null) : null;
}

/**
 * The Rules tab — the 'Chosen — Rules' board (design canvas row 'Chosen'):
 * the rule list on the left, the focused rule drawn as a vertical stage flow
 * in the middle (relationship ghost → trigger → condition → workflow →
 * action → `+`), its last runs on the right (runs are contextual, never a
 * tab of their own). Toggle, edit, delete (with undo), relationship editing
 * by direct manipulation, creation from "New rule" ("When does this happen?") and
 * answering pending human asks all happen here.
 */
export function Rules() {
  const { ruleId } = useParams();
  const navigate = useNavigate();
  const data = useRulesData(ruleId);
  const { rules, selected, loaded } = data;

  const [editing, setEditing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [adding, setAdding] = useState<StageChoice | null>(null);
  const [menu, setMenu] = useState(false);
  const [dragging, setDragging] = useState<string | null>(null);

  // What was open belongs to the rule that was focused.
  useEffect(() => {
    setEditing(false);
    setAdding(null);
    setMenu(false);
  }, [selected?.id]);

  const slots = useMemo(() => machineColors(data.machines.map((m) => m.name)), [data.machines]);
  const slotOf = (rule: RuleDoc) => slotFor(slots, rule);

  const { runs, settled: runsSettled, refreshHistory } = useRuleHistory(selected?.id);

  const { refreshRules, refreshAsks } = data;
  const onLive = useCallback(
    (changes: LiveChange[]) =>
      routeLiveChanges(changes, { rules: refreshRules, asks: refreshAsks, history: refreshHistory }),
    [refreshRules, refreshAsks, refreshHistory],
  );
  const live = useLiveUpdates(LIVE_COLLECTIONS, onLive);

  const asksSettled = !selected || data.asks !== null;
  const errors = [
    ...data.loadErrors,
    ...(runs?.error ? [runs.error] : []),
    ...(data.asks?.error ? [data.asks.error] : []),
  ];
  useTabReady("rules", loaded !== null && runsSettled && asksSettled, errors);

  const stages = selected ? stagesOf(selected) : [];
  useEffect(() => {
    setAgentState({
      rules: loaded ? { count: rules.length, selected: selected?.id ?? null, stages } : null,
    });
  }, [loaded, rules.length, selected?.id, stages.join(",")]);

  const nameOf = (id: string) => rules.find((r) => r.id === id)?.name ?? id;
  const now = Date.now();
  const { deleted, dismiss, removeSelected, undo } = useRemoveWithUndo(data);
  const alerts = [...errors, ...(data.notice ? [data.notice] : [])];

  const onCreate = async (doc: RuleDoc) => {
    const made = await data.create(doc);
    if (made) {
      setCreating(false);
      void navigate(`/rules/${encodeURIComponent(made.id)}`); // synchronous, see useRemoveWithUndo
    }
    return made !== null;
  };

  let flow: ReactNode;
  if (creating) {
    flow = <NewRuleForm takenIds={rules.map((r) => r.id)} onCancel={() => setCreating(false)} onCreate={onCreate} />;
  } else if (selected) {
    flow = (
      <FocusedRule
        rule={selected}
        data={data}
        stages={stages}
        slot={slotOf(selected)}
        editing={editing}
        setEditing={setEditing}
        adding={adding}
        setAdding={setAdding}
        menu={menu}
        setMenu={setMenu}
        dragging={dragging}
        nameOf={nameOf}
        onDelete={removeSelected}
      />
    );
  } else if (loaded && errors.length === 0) {
    flow = <h1 className="rule-title">No rules yet</h1>;
  } else {
    flow = <h1 className="rule-title sr-only">Rules</h1>;
  }

  return (
    <div className="rules-board">
      <RuleList
        rules={rules}
        selectedId={creating ? null : (selected?.id ?? null)}
        slotOf={slotOf}
        onToggle={data.toggle}
        pending={data.togglePending}
        onNew={() => setCreating(true)}
        onDragRule={setDragging}
      />

      <main id="main" className="rule-flow" tabIndex={-1} data-live-flash={live.flash || undefined}>
        {alerts.length > 0 ? (
          <p className="notice notice--error" role="alert">
            {alerts.join(" · ")}
          </p>
        ) : null}
        {deleted ? (
          <output className="notice notice--undo">
            <span>Deleted {deleted.name}</span>
            <button type="button" className="btn" onClick={undo}>
              Undo
            </button>
            <button type="button" className="icon-button icon-button--small" aria-label="Dismiss" onClick={dismiss}>
              ×
            </button>
          </output>
        ) : null}
        {flow}
      </main>

      <LastRuns items={runs?.items ?? []} now={now} nameOf={nameOf} />
    </div>
  );
}

export default Rules;
