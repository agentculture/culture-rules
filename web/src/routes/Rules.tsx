import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, listRuns } from "../api/client";
import type { RuleDoc } from "../api/rules";
import type { RunSummary } from "../api/types";
import { setAgentState } from "../agent-state/store";
import { machineColors } from "../culture-design/chart";
import { AddStageButton, Stage, StageArrow, machineStyle } from "../culture-design/stages";
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

/**
 * The Rules tab — the 'Chosen — Rules' board (design canvas row 'Chosen'):
 * the rule list on the left, the focused rule drawn as a vertical stage flow
 * in the middle (relationship ghost → trigger → condition → workflow →
 * action → `+`), its last runs on the right (runs are contextual, never a
 * tab of their own). Toggle, edit, delete (with undo), relationship editing
 * by direct manipulation, creation from "When does this happen?" and
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
  const [deleted, setDeleted] = useState<RuleDoc | null>(null);

  // What was open belongs to the rule that was focused.
  useEffect(() => {
    setEditing(false);
    setAdding(null);
    setMenu(false);
  }, [selected?.id]);

  const slots = useMemo(() => machineColors(data.machines.map((m) => m.name)), [data.machines]);
  const slotOf = (rule: RuleDoc) => {
    const machine = rule.placement?.machine;
    return machine && slots.has(machine) ? (slots.get(machine) as number) : null;
  };

  const [runs, setRuns] = useState<{ ruleId: string; items: RunSummary[]; error: string | null } | null>(null);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    listRuns({ rule_id: selected.id, limit: 4 }, controller.signal)
      .then((items) => setRuns({ ruleId: selected.id, items, error: null }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setRuns({
          ruleId: selected.id,
          items: [],
          error: err instanceof ApiError ? err.message : String(err),
        });
      });
    return () => controller.abort();
  }, [selected?.id]);

  const runsSettled = !selected || runs?.ruleId === selected.id;
  const asksSettled = !selected || data.asks !== null;
  const errors = [
    ...data.loadErrors,
    ...(runsSettled && runs?.error ? [runs.error] : []),
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
  const workflowName = (id: string) => data.workflows.find((w) => w.id === id)?.name ?? id;
  const now = Date.now();

  // ---- relationships: an edit is a PUT of the rule that declares it
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

  const removeSelected = async () => {
    if (!selected) return;
    const index = rules.findIndex((r) => r.id === selected.id);
    const next = rules[index + 1] ?? rules[index - 1];
    if (await data.remove(selected)) {
      setDeleted(selected);
      navigate(next ? `/rules/${encodeURIComponent(next.id)}` : "/rules", { replace: true });
    }
  };
  const undo = async () => {
    if (!deleted) return;
    const doc = await data.restore(deleted);
    if (doc) {
      setDeleted(null);
      navigate(`/rules/${encodeURIComponent(doc.id)}`);
    }
  };

  const { outgoing, incoming } = selected ? relationsOf(rules, selected.id) : { outgoing: [], incoming: [] };
  const alerts = [...errors, ...(data.notice ? [data.notice] : [])];

  const flow: ReactNode = creating ? (
    <NewRuleForm
      takenIds={rules.map((r) => r.id)}
      onCancel={() => setCreating(false)}
      onCreate={async (doc) => {
        const made = await data.create(doc);
        if (made) {
          setCreating(false);
          navigate(`/rules/${encodeURIComponent(made.id)}`);
        }
        return made !== null;
      }}
    />
  ) : selected ? (
    <>
      <div className="rule-flow__head">
        <h1 className="rule-title">{selected.name}</h1>
        <button
          type="button"
          className="placement-chip"
          data-machine-slot={slotOf(selected) ?? "none"}
          style={machineStyle(slotOf(selected))}
          onClick={() => setEditing(true)}
        >
          {selected.placement?.machine
            ? `on ${selected.placement.machine}`
            : selected.placement?.actor
              ? `via ${selected.placement.actor}`
              : "anywhere"}{" "}
          <span aria-hidden="true">▾</span>
        </button>
        <button type="button" className="icon-button" aria-label="Edit rule" aria-pressed={editing} onClick={() => setEditing((e) => !e)}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M4 20h4L19 9l-4-4L4 16v4z" />
          </svg>
        </button>
        <button type="button" className="icon-button icon-button--danger" aria-label="Delete rule" onClick={removeSelected}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
          </svg>
        </button>
      </div>

      {editing ? (
        <RuleEditForm key={selected.id} rule={selected} machines={data.machines} onSave={data.save} onCancel={() => setEditing(false)} />
      ) : null}

      <AsksPanel asks={data.asks?.items ?? []} onAnswer={data.answer} />

      <RelationSlots rules={rules} focused={selected} dragging={dragging} onAdd={(kind, id) => relate(selected, kind, id)} onMove={moveRelation} />

      {outgoing.map((rel, i) => (
        <Fragment key={`${rel.kind}-${rel.to}`}>
          <RelationCard
            rel={rel}
            direction="out"
            nameOf={nameOf}
            chip={rel.kind === "must_after" && i === 0 ? upstreamVars(selected).join(", ") : undefined}
            onRemove={unrelate}
          />
          <span className="relationship-link" aria-hidden="true" />
        </Fragment>
      ))}

      <ol className="stages" aria-label="Stages">
        {stages.map((kind, i) => (
          <li key={kind} className="stages__item">
            {i > 0 ? <StageArrow /> : null}
            {kind === "trigger" ? <Stage kind="trigger" label={triggerLabel(selected)} /> : null}
            {kind === "condition" && selected.condition ? (
              <Stage
                kind="condition"
                label={(() => {
                  const text = conditionText(selected.condition);
                  return (
                    <>
                      {text.subject ? <span className="mono-var">{text.subject}</span> : null}
                      {text.subject ? " " : null}
                      {text.rest}
                    </>
                  );
                })()}
              />
            ) : null}
            {kind === "workflow" && selected.workflow ? (
              <Stage kind="workflow" label={workflowName(selected.workflow.id)} chips={workflowChips(selected.workflow)} />
            ) : null}
            {kind === "action" ? (
              <Stage kind="action" label={selected.action.name || selected.action.kind} chips={actionChips(selected.action)} />
            ) : null}
          </li>
        ))}
      </ol>

      {adding ? (
        <AddStageForm key={adding} rule={selected} workflows={data.workflows} choice={adding} onSave={data.save} onCancel={() => setAdding(null)} />
      ) : (
        <AddStageButton onClick={() => setMenu((m) => !m)} />
      )}
      {menu && !adding ? (
        <div className="stage-menu" role="group" aria-label="Add a stage">
          <button type="button" className="btn" disabled={!!selected.condition} onClick={() => { setAdding("condition"); setMenu(false); }}>
            Add condition
          </button>
          <button type="button" className="btn" disabled={!!selected.workflow || data.workflows.length === 0} onClick={() => { setAdding("workflow"); setMenu(false); }}>
            Add workflow
          </button>
        </div>
      ) : null}

      {incoming.length > 0 ? (
        <div className="relationship-after">
          {incoming.map((rel) => (
            <RelationCard key={`${rel.kind}-${rel.from}`} rel={rel} direction="in" nameOf={nameOf} onRemove={unrelate} />
          ))}
        </div>
      ) : null}
    </>
  ) : loaded && errors.length === 0 ? (
    <h1 className="rule-title">No rules yet</h1>
  ) : (
    <h1 className="rule-title sr-only">Rules</h1>
  );

  return (
    <div className="rules-board">
      <RuleList
        rules={rules}
        selectedId={creating ? null : (selected?.id ?? null)}
        slotOf={slotOf}
        onToggle={data.toggle}
        onNew={() => setCreating(true)}
        onDragRule={setDragging}
      />

      <main id="main" className="rule-flow" tabIndex={-1}>
        {alerts.length > 0 ? (
          <p className="notice notice--error" role="alert">
            {alerts.join(" · ")}
          </p>
        ) : null}
        {deleted ? (
          <p className="notice notice--undo" role="status">
            <span>Deleted {deleted.name}</span>
            <button type="button" className="btn" onClick={undo}>
              Undo
            </button>
            <button type="button" className="icon-button icon-button--small" aria-label="Dismiss" onClick={() => setDeleted(null)}>
              ×
            </button>
          </p>
        ) : null}
        {flow}
      </main>

      <aside className="last-runs" aria-label="Last runs">
        <span className="last-runs__title">Last runs</span>
        <ul className="last-runs__list">
          {(runsSettled && runs ? runs.items : []).map((run) => (
            <li key={run.id} className="last-runs__run" data-run-status={run.status}>
              <span className={`run-dot run-dot--${run.status}`} aria-hidden="true" />
              <span className="sr-only">{run.status}, </span>
              {ago(run.created_at, now)}
            </li>
          ))}
        </ul>
      </aside>
    </div>
  );
}

export default Rules;
