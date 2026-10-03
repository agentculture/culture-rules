import { useState, type FormEvent, type KeyboardEvent } from "react";
import type { Ask, RuleDoc } from "../api/rules";
import type { Machine, Workflow } from "../api/types";
import { slugFor, triggerLabel } from "../routes/rules-view";

const KEEP = "__keep__";

const onEscape = (cancel: () => void) => (e: KeyboardEvent) => {
  if (e.key === "Escape") {
    e.stopPropagation();
    cancel();
  }
};

interface EditProps {
  rule: RuleDoc;
  machines: Machine[];
  onSave: (rule: RuleDoc) => Promise<boolean>;
  onCancel: () => void;
}

/** Edit a rule in place: its name, trigger words, action name and placement. */
export function RuleEditForm({ rule, machines, onSave, onCancel }: EditProps) {
  const [name, setName] = useState(rule.name);
  const [trigger, setTrigger] = useState(triggerLabel(rule));
  const [action, setAction] = useState(rule.action.name ?? "");
  const placed = rule.placement?.machine ?? "";
  const foreign = !placed && (rule.placement?.actor || rule.placement?.requirement?.length);
  const [placement, setPlacement] = useState(foreign ? KEEP : placed);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const label = trigger.trim();
    const next: RuleDoc = {
      ...rule,
      name: name.trim(),
      trigger:
        label && label !== triggerLabel(rule)
          ? { ...rule.trigger, params: { ...rule.trigger.params, label } }
          : rule.trigger,
      action: { ...rule.action, name: action.trim() },
      placement:
        placement === KEEP ? rule.placement : placement ? { machine: placement } : null,
    };
    if (await onSave(next)) onCancel();
  };

  return (
    <form className="rule-form" aria-label="Edit rule" onSubmit={submit} onKeyDown={onEscape(onCancel)}>
      <label>
        <span>Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} required autoFocus />
      </label>
      <label>
        <span>Trigger</span>
        <input value={trigger} onChange={(e) => setTrigger(e.target.value)} />
      </label>
      <label>
        <span>Action</span>
        <input value={action} onChange={(e) => setAction(e.target.value)} />
      </label>
      <label>
        <span>Placement</span>
        <select value={placement} onChange={(e) => setPlacement(e.target.value)}>
          <option value="">Anywhere</option>
          {foreign ? (
            <option value={KEEP}>
              {rule.placement?.actor ? `via ${rule.placement.actor}` : "by capability"}
            </option>
          ) : null}
          {machines.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
            </option>
          ))}
        </select>
      </label>
      <div className="rule-form__actions">
        <button type="submit" className="btn btn--primary">
          Save
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

interface NewProps {
  takenIds: string[];
  onCreate: (rule: RuleDoc) => Promise<boolean>;
  onCancel: () => void;
}

const TRIGGER_KINDS = ["event", "schedule", "manual"];

/**
 * Progressive creation: ask only "When does this happen?" and make a rule of
 * a trigger (the API requires an action, so it starts with a placeholder
 * `mesh.message` the user renames); everything else grows through the `+`.
 */
export function NewRuleForm({ takenIds, onCreate, onCancel }: NewProps) {
  const [label, setLabel] = useState("");
  const [kind, setKind] = useState("event");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const text = label.trim();
    if (!text) return;
    await onCreate({
      id: slugFor(text, takenIds),
      name: text,
      trigger: { kind, params: { label: text } },
      action: { kind: "mesh.message", name: "Notify" },
      enabled: true,
    });
  };
  return (
    <form className="rule-form rule-form--new" aria-label="New rule" onSubmit={submit} onKeyDown={onEscape(onCancel)}>
      <h2 className="rule-form__title">When does this happen?</h2>
      <label>
        <span>Trigger</span>
        <input value={label} onChange={(e) => setLabel(e.target.value)} required autoFocus />
      </label>
      <label>
        <span>Kind</span>
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          {TRIGGER_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </label>
      <div className="rule-form__actions">
        <button type="submit" className="btn btn--primary">
          Create rule
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

export type StageChoice = "condition" | "workflow";

interface AddProps {
  rule: RuleDoc;
  workflows: Workflow[];
  choice: StageChoice;
  onSave: (rule: RuleDoc) => Promise<boolean>;
  onCancel: () => void;
}

/** The `+` forms: a simple condition (variable, is / is not, value) or a workflow. */
export function AddStageForm({ rule, workflows, choice, onSave, onCancel }: AddProps) {
  const [variable, setVariable] = useState("");
  const [cmp, setCmp] = useState("==");
  const [value, setValue] = useState("");
  const [workflow, setWorkflow] = useState(workflows[0]?.id ?? "");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const next: RuleDoc =
      choice === "condition"
        ? {
            ...rule,
            condition: {
              op: "compare",
              cmp,
              left: { var: variable.trim() },
              right: { literal: value },
            },
          }
        : { ...rule, workflow: { id: workflow, inputs: {} } };
    if (await onSave(next)) onCancel();
  };

  const title = choice === "condition" ? "Add condition" : "Add workflow";
  return (
    <form className="rule-form rule-form--inline" aria-label={title} onSubmit={submit} onKeyDown={onEscape(onCancel)}>
      {choice === "condition" ? (
        <>
          <label>
            <span>Variable</span>
            <input value={variable} onChange={(e) => setVariable(e.target.value)} required autoFocus />
          </label>
          <label>
            <span>Comparison</span>
            <select value={cmp} onChange={(e) => setCmp(e.target.value)}>
              <option value="==">is</option>
              <option value="!=">is not</option>
            </select>
          </label>
          <label>
            <span>Value</span>
            <input value={value} onChange={(e) => setValue(e.target.value)} required />
          </label>
        </>
      ) : (
        <label>
          <span>Workflow</span>
          <select value={workflow} onChange={(e) => setWorkflow(e.target.value)} autoFocus>
            {workflows.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="rule-form__actions">
        <button type="submit" className="btn btn--primary" disabled={choice === "workflow" && !workflow}>
          Add
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

interface AsksProps {
  asks: Ask[];
  onAnswer: (ask: Ask, answer: string) => Promise<boolean>;
}

/** Pending human asks of this rule's waiting runs, answerable where they arise. */
export function AsksPanel({ asks, onAnswer }: AsksProps) {
  const [text, setText] = useState<Record<string, string>>({});
  if (asks.length === 0) return null;
  return (
    <section className="asks" aria-label="Waiting on you">
      <h2 className="asks__title">Waiting on you</h2>
      <ul className="asks__list">
        {asks.map((ask) => (
          <li key={ask.id} className="ask" data-ask-id={ask.id}>
            <p className="ask__question">{ask.question}</p>
            {ask.options && ask.options.length > 0 ? (
              <div className="ask__options">
                {ask.options.map((option) => (
                  <button key={option} type="button" className="btn" onClick={() => onAnswer(ask, option)}>
                    {option}
                  </button>
                ))}
              </div>
            ) : (
              <form
                className="ask__text"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (text[ask.id]?.trim()) void onAnswer(ask, text[ask.id].trim());
                }}
              >
                <label>
                  <span className="sr-only">Answer</span>
                  <input
                    aria-label="Answer"
                    value={text[ask.id] ?? ""}
                    onChange={(e) => setText({ ...text, [ask.id]: e.target.value })}
                  />
                </label>
                <button type="submit" className="btn btn--primary">
                  Send answer
                </button>
              </form>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
