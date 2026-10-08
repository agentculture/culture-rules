import { useEffect, useRef, useState, type FormEvent } from "react";
import type { Actor } from "../api/actors";
import type { Ask, RuleDoc } from "../api/rules";
import type { Action, Condition, Machine, Operand, Trigger, Workflow } from "../api/types";
import { listVariables, type Variable } from "../api/variables";
import GuidedNotice from "../components/GuidedNotice";
import { useEscapeKey } from "../hooks/useEscapeKey";
import { slugFor } from "../routes/rules-view";
import ActionPicker, { actionProblem } from "./ActionPicker";
import TriggerPicker, { blankTrigger, triggerProblem } from "./TriggerPicker";

const KEEP = "__keep__";

/** The placement the edit form saves: kept as is, a machine, or anywhere (null). */
function chosenPlacement(choice: string, rule: RuleDoc): RuleDoc["placement"] {
  if (choice === KEEP) return rule.placement;
  return choice ? { machine: choice } : null;
}

/**
 * A form's keyboard contract: Escape anywhere inside it cancels, and keyboard
 * focus lands in its first field when it opens (`autoFocus` without the
 * attribute; `focusKey` re-runs it when the form changes which field is first).
 */
function useFormKeyboard<F extends HTMLElement>(onCancel: () => void, focusKey?: unknown) {
  const form = useRef<HTMLFormElement>(null);
  const first = useRef<F>(null);
  useEscapeKey(form, onCancel);
  useEffect(() => {
    first.current?.focus();
  }, [focusKey]);
  return { form, first };
}

/** A changed trigger no longer matches its display label, so the label is dropped. */
function withoutLabel(trigger: Trigger): Trigger {
  if (!trigger.params || !("label" in trigger.params)) return trigger;
  const { label: _label, ...params } = trigger.params as Record<string, unknown>;
  return { ...trigger, params } as Trigger;
}

/** The event type an action's trigger fields are offered for; label-only triggers have none. */
const triggerTypeOf = (t: Trigger) =>
  t.kind === "event" ? (t.params as { type?: string } | undefined)?.type || undefined : undefined;

/** An action without an empty name (the name is optional). */
function withoutEmptyName(action: Action): Action {
  if (action.name !== "") return action;
  const { name: _name, ...rest } = action;
  return rest;
}

interface EditProps {
  rule: RuleDoc;
  machines: Machine[];
  workflows?: Workflow[];
  actors: Actor[];
  onSave: (rule: RuleDoc) => Promise<boolean>;
  onCancel: () => void;
}

/** Edit a rule in place: its name, typed trigger, typed action and placement. */
export function RuleEditForm({ rule, machines, workflows = [], actors, onSave, onCancel }: Readonly<EditProps>) {
  const [name, setName] = useState(rule.name);
  const [trigger, setTrigger] = useState<Trigger>(rule.trigger);
  const [problem, setProblem] = useState<string | null>(null);
  const [action, setAction] = useState<Action>(rule.action);
  const [actionIssue, setActionIssue] = useState<string | null>(null);
  const placed = rule.placement?.machine ?? "";
  const foreign = !placed && (rule.placement?.actor || rule.placement?.requirement?.length);
  const [placement, setPlacement] = useState(foreign ? KEEP : placed);
  const { form, first } = useFormKeyboard<HTMLInputElement>(onCancel);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    // An untouched trigger is saved as it was (a legacy label-only one stays valid);
    // a changed one must be complete, and drops the label it no longer matches.
    const changed = JSON.stringify(trigger) !== JSON.stringify(rule.trigger);
    const found = changed ? triggerProblem(trigger) : null;
    setProblem(found);
    // Likewise an untouched action is saved as it was; a changed one must be complete.
    const actionChanged = JSON.stringify(action) !== JSON.stringify(rule.action);
    const actionFound = actionChanged ? actionProblem(action) : null;
    setActionIssue(actionFound);
    if (found || actionFound) return;
    const next: RuleDoc = {
      ...rule,
      name: name.trim(),
      trigger: changed ? withoutLabel(trigger) : rule.trigger,
      action: actionChanged ? withoutEmptyName({ ...action, name: action.name?.trim() }) : rule.action,
      placement: chosenPlacement(placement, rule),
    };
    if (await onSave(next)) onCancel();
  };

  return (
    <form ref={form} className="rule-form" aria-label="Edit rule" onSubmit={submit}>
      <label>
        <span>Name</span>
        <input ref={first} value={name} onChange={(e) => setName(e.target.value)} required />
      </label>
      <TriggerPicker value={trigger} actors={actors} onChange={setTrigger} />
      {problem ? <GuidedNotice code={problem} /> : null}
      <ActionPicker
        value={action}
        actors={actors}
        triggerType={triggerTypeOf(trigger)}
        workflow={workflows.find((w) => w.id === rule.workflow?.id)}
        onChange={setAction}
      />
      {actionIssue ? <GuidedNotice code={actionIssue} /> : null}
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
  actors: Actor[];
  takenIds: string[];
  onCreate: (rule: RuleDoc) => Promise<boolean>;
  onCancel: () => void;
}

/** The API requires an action, so a new rule starts with this placeholder until one is picked. */
const PLACEHOLDER_ACTION: Action = { kind: "mesh.message", name: "Notify" };

/**
 * Progressive creation: ask "When does this happen?" then "Then what happens?"
 * (starting from the `mesh.message` placeholder); everything else grows through the `+`.
 */
export function NewRuleForm({ actors, takenIds, onCreate, onCancel }: Readonly<NewProps>) {
  const [name, setName] = useState("");
  const [trigger, setTrigger] = useState<Trigger>(blankTrigger("event"));
  const [action, setAction] = useState<Action>(PLACEHOLDER_ACTION);
  const [problem, setProblem] = useState<string | null>(null);
  const [actionIssue, setActionIssue] = useState<string | null>(null);
  const { form, first } = useFormKeyboard<HTMLInputElement>(onCancel);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const text = name.trim();
    if (!text) return;
    const found = triggerProblem(trigger);
    setProblem(found);
    const actionFound = actionProblem(action);
    setActionIssue(actionFound);
    if (found || actionFound) return;
    await onCreate({
      id: slugFor(text, takenIds),
      name: text,
      trigger,
      action: withoutEmptyName({ ...action, name: action.name?.trim() }),
      enabled: true,
    });
  };
  return (
    <form ref={form} className="rule-form rule-form--new" aria-label="New rule" onSubmit={submit}>
      <label>
        <span>Name</span>
        <input ref={first} value={name} onChange={(e) => setName(e.target.value)} required />
      </label>
      <TriggerPicker value={trigger} actors={actors} onChange={setTrigger} />
      {problem ? <GuidedNotice code={problem} /> : null}
      <ActionPicker value={action} actors={actors} triggerType={triggerTypeOf(trigger)} onChange={setAction} />
      {actionIssue ? <GuidedNotice code={actionIssue} /> : null}
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

const TYPED_LIST = "__typed__";

/**
 * What the author typed for the left side: `trigger.data.author` reads the trigger's own
 * field (`{field: "data.author"}`); anything else names a variable (`{var}`).
 */
function leftOperand(text: string): Operand {
  const typed = text.trim();
  if (typed.startsWith("trigger.") && typed.length > "trigger.".length) {
    return { field: typed.slice("trigger.".length) };
  }
  return { var: typed.replace(/^vars\./, "") };
}

/** Variables whose value is a list: the ones an `is one of` check can read. */
const listVariablesOf = (variables: Variable[]) => variables.filter((v) => Array.isArray(v.value));

/** The `+` forms: a simple condition (variable, is / is not / is one of, value) or a workflow. */
export function AddStageForm({ rule, workflows, choice, onSave, onCancel }: Readonly<AddProps>) {
  const [variable, setVariable] = useState("");
  const [cmp, setCmp] = useState("==");
  const [value, setValue] = useState("");
  const [list, setList] = useState("");
  const [typed, setTyped] = useState("");
  const [variables, setVariables] = useState<Variable[]>([]);
  const [workflow, setWorkflow] = useState(workflows[0]?.id ?? "");
  // The first field is the Variable input or the Workflow select, by `choice`.
  const { form, first } = useFormKeyboard<HTMLInputElement & HTMLSelectElement>(onCancel, choice);

  useEffect(() => {
    if (choice !== "condition") return;
    const controller = new AbortController();
    // No variables to pick from is not an error here: the typed list still works.
    listVariables(controller.signal)
      .then(setVariables)
      .catch(() => undefined);
    return () => controller.abort();
  }, [choice]);

  const membership = cmp === "in";
  const items = (): Operand =>
    list === TYPED_LIST || !list
      ? {
          literal: typed
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
        }
      : { var: list };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    let condition: Condition;
    if (membership) {
      condition = { op: "in", value: leftOperand(variable), items: items() };
    } else {
      condition = { op: "compare", cmp, left: { var: variable.trim() }, right: { literal: value } };
    }
    const next: RuleDoc =
      choice === "condition" ? { ...rule, condition } : { ...rule, workflow: { id: workflow, inputs: {} } };
    if (await onSave(next)) onCancel();
  };

  const title = choice === "condition" ? "Add condition" : "Add workflow";
  const lists = listVariablesOf(variables);
  const typingList = membership && (list === TYPED_LIST || lists.length === 0);
  return (
    <form ref={form} className="rule-form rule-form--inline" aria-label={title} onSubmit={submit}>
      {choice === "condition" ? (
        <>
          <label>
            <span>Variable</span>
            <input
              ref={first}
              value={variable}
              placeholder={membership ? "trigger.data.author" : undefined}
              onChange={(e) => setVariable(e.target.value)}
              required
            />
          </label>
          <label>
            <span>Comparison</span>
            <select value={cmp} onChange={(e) => setCmp(e.target.value)}>
              <option value="==">is</option>
              <option value="!=">is not</option>
              <option value="in">is one of</option>
            </select>
          </label>
          {membership ? (
            <>
              <label>
                <span>Allowed list</span>
                <select value={list || (lists.length === 0 ? TYPED_LIST : "")} onChange={(e) => setList(e.target.value)} required>
                  <option value="" disabled>
                    Pick a variable…
                  </option>
                  {lists.map((v) => (
                    <option key={v.name} value={v.name}>
                      vars.{v.name}
                    </option>
                  ))}
                  <option value={TYPED_LIST}>A list I type</option>
                </select>
              </label>
              {typingList ? (
                <label>
                  <span>Items</span>
                  <input value={typed} placeholder="main, dev" onChange={(e) => setTyped(e.target.value)} required />
                </label>
              ) : null}
            </>
          ) : (
            <label>
              <span>Value</span>
              <input value={value} onChange={(e) => setValue(e.target.value)} required />
            </label>
          )}
        </>
      ) : (
        <label>
          <span>Workflow</span>
          <select ref={first} value={workflow} onChange={(e) => setWorkflow(e.target.value)}>
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
export function AsksPanel({ asks, onAnswer }: Readonly<AsksProps>) {
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
