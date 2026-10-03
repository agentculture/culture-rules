import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, listMachines, listRules, listRuns, listWorkflows } from "../api/client";
import type { Machine, Rule, RunSummary, Workflow } from "../api/types";
import { setAgentState } from "../agent-state/store";
import { machineColors } from "../culture-design/chart";
import {
  AddStageButton,
  MachineDot,
  RelationshipCard,
  Stage,
  StageArrow,
  Switch,
  machineStyle,
} from "../culture-design/stages";
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

interface Loaded {
  rules: Rule[];
  machines: Machine[];
  workflows: Workflow[];
  errors: string[];
}

const message = (r: PromiseSettledResult<unknown>) =>
  r.status === "rejected"
    ? r.reason instanceof ApiError
      ? r.reason.message
      : String(r.reason)
    : null;

/**
 * The Rules tab — the 'Chosen — Rules' board (design canvas row 'Chosen'):
 * the rule list on the left, the selected rule drawn as a vertical stage
 * flow in the middle, its last runs on the right (runs are contextual,
 * never a tab of their own). Editing behaviour arrives with the Rules tab
 * task; this is the shell's layout and read path.
 */
export function Rules() {
  const { ruleId } = useParams();
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [runs, setRuns] = useState<{ ruleId: string; items: RunSummary[]; error: string | null } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.allSettled([
      listRules(controller.signal),
      listMachines(controller.signal),
      listWorkflows(controller.signal),
    ]).then((results) => {
      if (controller.signal.aborted) return;
      const [rules, machines, workflows] = results;
      setLoaded({
        rules: rules.status === "fulfilled" ? rules.value : [],
        machines: machines.status === "fulfilled" ? machines.value : [],
        workflows: workflows.status === "fulfilled" ? workflows.value : [],
        errors: results.map(message).filter((m): m is string => m !== null),
      });
    });
    return () => controller.abort();
  }, []);

  const rules = loaded?.rules ?? [];
  const selected = rules.find((r) => r.id === ruleId) ?? rules[0] ?? null;
  const slots = useMemo(
    () => machineColors((loaded?.machines ?? []).map((m) => m.name)),
    [loaded?.machines],
  );
  const slotOf = (rule: Rule) => {
    const machine = rule.placement?.machine;
    return machine && slots.has(machine) ? (slots.get(machine) as number) : null;
  };

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
  const errors = [...(loaded?.errors ?? []), ...(runsSettled && runs?.error ? [runs.error] : [])];
  const ready = loaded !== null && runsSettled;
  useTabReady("rules", ready, errors);

  const stages = selected ? stagesOf(selected) : [];
  useEffect(() => {
    setAgentState({
      rules: loaded ? { count: rules.length, selected: selected?.id ?? null, stages } : null,
    });
  }, [loaded, rules.length, selected?.id, stages.join(",")]);

  const nameOf = (id: string) => rules.find((r) => r.id === id)?.name ?? id;
  const workflowName = (id: string) =>
    loaded?.workflows.find((w) => w.id === id)?.name ?? id;
  const now = Date.now();

  return (
    <div className="rules-board">
      <nav className="rule-list" aria-label="Rules">
        <button type="button" className="rule-list__new">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
            <path d="M12 5v14M5 12h14" />
          </svg>
          When does this happen?
        </button>
        {rules.map((rule) => {
          const enabled = rule.enabled !== false;
          const isSelected = rule.id === selected?.id;
          return (
            <div
              key={rule.id}
              className={`rule-row${isSelected ? " is-selected" : ""}${enabled ? "" : " is-disabled"}`}
              data-rule-id={rule.id}
              style={machineStyle(slotOf(rule))}
            >
              <MachineDot slot={slotOf(rule)} />
              <Link
                className="rule-row__name"
                to={`/rules/${encodeURIComponent(rule.id)}`}
                aria-current={isSelected ? "true" : undefined}
              >
                {rule.name}
              </Link>
              <Switch label={`${rule.name} enabled`} checked={enabled} />
            </div>
          );
        })}
      </nav>

      <main id="main" className="rule-flow" tabIndex={-1}>
        {errors.length > 0 ? (
          <p className="notice notice--error" role="alert">
            {errors.join(" · ")}
          </p>
        ) : null}
        {selected ? (
          <>
            <div className="rule-flow__head">
              <h1 className="rule-title">{selected.name}</h1>
              <button
                type="button"
                className="placement-chip"
                data-machine-slot={slotOf(selected) ?? "none"}
                style={machineStyle(slotOf(selected))}
              >
                {selected.placement?.machine
                  ? `on ${selected.placement.machine}`
                  : selected.placement?.actor
                    ? `via ${selected.placement.actor}`
                    : "anywhere"}{" "}
                <span aria-hidden="true">▾</span>
              </button>
              <button type="button" className="icon-button" aria-label="Edit rule">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M4 20h4L19 9l-4-4L4 16v4z" />
                </svg>
              </button>
              <button type="button" className="icon-button icon-button--danger" aria-label="Delete rule">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
                </svg>
              </button>
            </div>

            {(selected.must_after ?? []).map((id, i) => (
              <Fragment key={`must-${id}`}>
                <RelationshipCard chip={i === 0 ? upstreamVars(selected).join(", ") : undefined}>
                  must run after <strong>{nameOf(id)}</strong>
                </RelationshipCard>
                <span className="relationship-link" aria-hidden="true" />
              </Fragment>
            ))}
            {(selected.may_after ?? []).map((id) => (
              <Fragment key={`may-${id}`}>
                <RelationshipCard>
                  may run after <strong>{nameOf(id)}</strong>
                </RelationshipCard>
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
                    <Stage
                      kind="workflow"
                      label={workflowName(selected.workflow.id)}
                      chips={workflowChips(selected.workflow)}
                    />
                  ) : null}
                  {kind === "action" ? (
                    <Stage
                      kind="action"
                      label={selected.action.name || selected.action.kind}
                      chips={actionChips(selected.action)}
                    />
                  ) : null}
                </li>
              ))}
            </ol>
            <AddStageButton />
          </>
        ) : loaded && errors.length === 0 ? (
          <h1 className="rule-title">No rules yet</h1>
        ) : (
          <h1 className="rule-title sr-only">Rules</h1>
        )}
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
