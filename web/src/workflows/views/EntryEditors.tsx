/**
 * The entry-point and Then-side editors, in Detailed (spec c13, task t15).
 *
 * Simple edits a workflow's rules through these same components; Simple is
 * turning into text only, so Detailed carries every editor it drops. They are
 * imported from ../simple, not copied: EntryCard (the entry trigger,
 * condition, placement and attempt counting, continues from, enable and
 * delete), PlacementForm and AddStageForm (placement and condition shared by
 * every entry point) and ThenColumn (continues into, ends here, on failure,
 * runs). Only the glue is here: loading the rules, fanning a shared edit out
 * rule by rule through the fold writes, and the overrides each entry carries.
 */
import { useMemo, useState } from "react";
import type { Placement, Rule } from "../../api/types";
import type { WorkflowDef } from "../../api/workflows";
import { foldModel, predecessorTerms, type Continuation, type FoldEntry } from "../../fold/model";
import type { SharedRuleEdit } from "../../fold/writes";
import { AddStageForm, NewRuleForm } from "../../rules/Forms";
import { useRulesData } from "../../rules/useRulesData";
import "../../rules/rules.css";
import { ConditionRows, EntryCard, type Override } from "../simple/EntryCard";
import { PlacementForm } from "../simple/SharedForms";
import { ThenColumn } from "../simple/ThenColumn";
import { useFanout } from "../simple/useFanout";
import { WriteResults } from "../simple/WriteResults";
import {
  canonical, conditionRows, countsEdit, fieldOf, placementWords, split, triggerParts, valueText, withTerm,
  withoutEqualTerm,
} from "../simple/text";
import "../simple/simple.css";

const OVERRIDE_FIELDS = ["placement", "action", "on_failure", "concurrency_key", "max_attempts"] as const;
const LABELS: Record<string, string> = {
  action: "ends here",
  on_failure: "on failure",
  concurrency_key: "run key",
  max_attempts: "attempts",
};

const defaultHref = (id: string) => `/workflows?id=${encodeURIComponent(id)}`;

function overridesOf(rule: Rule, splits: Map<string, ReturnType<typeof split>>): Override[] {
  return OVERRIDE_FIELDS.flatMap((field) => {
    const s = splits.get(field);
    if (!s?.overrides.some((o) => o.rule.id === rule.id)) return [];
    const text = valueText(field, fieldOf(rule, field));
    return [{ field, text: field === "placement" ? text : `${LABELS[field] ?? field}: ${text}` }];
  });
}

function onwardOf(continuations: readonly Continuation[], workflowId: string): Continuation[] {
  return continuations.filter(
    (c) =>
      c.workflowId !== workflowId &&
      (c.fromWorkflowId === workflowId ||
        c.predecessor.kind === "any" ||
        (c.predecessor.kind === "ambiguous" && c.predecessor.workflowIds.includes(workflowId))),
  );
}

export interface EntryEditorsProps {
  workflow: WorkflowDef;
  hrefFor?: (workflowId: string) => string;
}

export function EntryEditors({ workflow, hrefFor = defaultHref }: Readonly<EntryEditorsProps>) {
  const workflowId = workflow.id;
  const [open, setOpen] = useState<string | null | undefined>(undefined);
  const data = useRulesData(open ?? undefined);
  const { refreshRules } = data;
  const model = useMemo(() => foldModel(data.rules, data.workflows), [data.rules, data.workflows]);
  const folded = model.workflows.find((w) => w.id === workflowId) ?? null;
  const entries: FoldEntry[] = useMemo(() => folded?.entries ?? [], [folded]);
  const rules = useMemo(() => entries.map((e) => e.rule), [entries]);
  const openId = open === undefined ? (entries[0]?.rule.id ?? null) : open;

  const fanout = useFanout(refreshRules);
  const [baselines, setBaselines] = useState<Record<string, { value: unknown }>>({});
  const [creating, setCreating] = useState(false);
  const [placing, setPlacing] = useState(false);
  const [addingShared, setAddingShared] = useState(false);
  const [deleted, setDeleted] = useState<Rule | null>(null);

  const splits = useMemo(
    () => new Map(OVERRIDE_FIELDS.map((f) => [f, split(rules, f, baselines[f])])),
    [rules, baselines],
  );
  const placement = splits.get("placement")!;
  const conditionShared =
    rules.length > 1 && rules.every((r) => r.condition && canonical(r.condition) === canonical(rules[0].condition));
  const types = [...new Set(rules.map((r) => (r.trigger.kind === "event" ? triggerParts(r.trigger).value : "")))];
  const onward = onwardOf(model.continuations as Continuation[], workflowId);

  const fanOut = (label: string, edit: Record<string, unknown>, snapshots: readonly Rule[] = rules) => {
    setBaselines((b) => ({ ...b, ...Object.fromEntries(Object.entries(edit).map(([f, value]) => [f, { value }])) }));
    void fanout.fanOut(label, snapshots, edit as SharedRuleEdit);
  };
  const conditionEach = (next: (rule: Rule) => Rule["condition"]) =>
    void fanout.fanOutEach("Condition", ["condition"], rules, (rule) => ({ condition: next(rule) }) as SharedRuleEdit);
  const override = (rule: Rule, label: string, edit: Record<string, unknown>) =>
    fanout.fanOut(label, [rule], edit as SharedRuleEdit);

  const remove = async (rule: Rule) => {
    if (await data.remove(rule)) setDeleted(rule);
  };
  const undo = async () => {
    if (!deleted) return;
    const doc = await data.restore(deleted);
    if (doc) {
      setDeleted(null);
      setOpen(doc.id);
    }
  };
  const onCreate = async (doc: Rule) => {
    const made = await data.create({ ...doc, workflow: { id: workflowId, inputs: {} } });
    if (made) {
      setCreating(false);
      setOpen(made.id);
    }
    return made !== null;
  };

  const alerts = [...data.loadErrors, ...(data.notice ? [data.notice] : [])];
  const sharedCondition = conditionShared ? conditionRows(rules[0].condition, predecessorTerms(rules[0].condition)) : [];

  return (
    <section className="wf-entry-editors fold-simple" aria-label="Entry points and Then">
      <h2 className="wf-panel__title">Entry points and Then</h2>
      {alerts.length > 0 ? (
        // role status, not alert: the page already raises its own alert for a failed load
        <p className="notice notice--error" role="status">
          {alerts.join(" · ")}
        </p>
      ) : null}
      {deleted ? (
        <output className="notice notice--undo">
          <span>Deleted {deleted.name}</span>
          <button type="button" className="btn" onClick={() => void undo()}>
            Undo
          </button>
          <button type="button" className="icon-button icon-button--small" aria-label="Dismiss" onClick={() => setDeleted(null)}>
            ×
          </button>
        </output>
      ) : null}
      {fanout.batch ? (
        <WriteResults batch={fanout.batch} busy={fanout.busy} onRetry={(id) => void fanout.retry(id)} onDismiss={fanout.dismiss} />
      ) : null}

      <div className="fold-board">
        <section className="fold-col fold-col--when" aria-label="Entry points">
          <div className="fold-col__head">
            <h3 className="fold-col__title">When</h3>
            <button type="button" className="fold-add fold-add--big" aria-expanded={creating} onClick={() => setCreating((c) => !c)}>
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
            <fieldset aria-label="Shared by every entry point" className="fold-shared plain-group">
              <button type="button" className="fold-shared__placement" aria-expanded={placing} onClick={() => setPlacing((p) => !p)}>
                {placement.shared || placement.baseline
                  ? `evaluates ${placementWords(placement.value as Placement | null | undefined)}`
                  : "evaluates: differs per entry point"}{" "}
                <span aria-hidden="true">▾</span>
              </button>
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
                    onRemove={(row) =>
                      conditionEach((rule) => withoutEqualTerm(rule.condition, row.node!, predecessorTerms(rule.condition)))
                    }
                  />
                  <button
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
                        conditionEach((rule) => withTerm(rule.condition, next.condition!));
                        return Promise.resolve(true);
                      }}
                      onCancel={() => setAddingShared(false)}
                    />
                  ) : null}
                </>
              ) : null}
            </fieldset>
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
              historyTick={0}
              onCounts={(rule) => {
                const edit = countsEdit(rule);
                if (edit) void override(rule, `${rule.name}: attempt counting`, edit);
              }}
              onPredecessor={(rule, id) => void fanout.predecessor(`${rule.name}: continues from`, rule, id)}
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

export default EntryEditors;
