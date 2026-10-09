import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import type { Actor } from "../../api/actors";
import type { Action, Machine, Placement, Rule, Workflow } from "../../api/types";
import GuidedNotice from "../../components/GuidedNotice";
import { useEscapeKey } from "../../hooks/useEscapeKey";
import ActionPicker, { actionProblem, blankAction } from "../../rules/ActionPicker";
import { runsProblem, type RunsEdit } from "./text";

/** Who a save writes: every entry point's rule (a shared value) or this entry's alone (an override). */
export type Scope = "every entry point" | "this entry point";

/**
 * The workflow-level forms of the Simple view. Each edits one value every
 * entry point holds (D3-D6) and hands it to the fold writes' fan-out, which
 * writes every entry point's rule one at a time. Escape cancels, focus lands
 * in the first field, like the Rules tab's forms.
 */
function SharedForm({
  label,
  scope = "every entry point",
  onSubmit,
  onCancel,
  busy,
  extra,
  children,
}: Readonly<{ label: string; scope?: Scope; onSubmit: () => void; onCancel: () => void; busy: boolean; extra?: ReactNode; children: ReactNode }>) {
  const form = useRef<HTMLFormElement>(null);
  useEscapeKey(form, onCancel);
  useEffect(() => {
    form.current?.querySelector<HTMLElement>("input, select, textarea")?.focus();
  }, []);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit();
  };
  return (
    <form ref={form} className="rule-form fold-shared-form" aria-label={label} onSubmit={submit}>
      {children}
      <div className="rule-form__actions">
        <button type="submit" className="btn btn--primary" disabled={busy}>
          Save for {scope}
        </button>
        {extra}
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/** An action without an empty label (the Rules tab's forms save it so). */
function withoutEmptyName(action: Action): Action {
  const name = action.name?.trim();
  if (name) return { ...action, name };
  const { name: _name, ...rest } = action;
  return rest;
}

/** "Ends here" and "On failure": the Rules tab's ActionPicker, saved for every entry point. */
export function SharedActionForm({
  label,
  scope = "every entry point",
  value,
  actors,
  triggerType,
  workflow,
  busy,
  onSave,
  onRemove,
  onCancel,
}: Readonly<{
  label: string;
  scope?: Scope;
  value: Action | null | undefined;
  actors: Actor[];
  triggerType?: string;
  workflow?: Workflow;
  busy: boolean;
  onSave: (action: Action) => void;
  onRemove?: () => void;
  onCancel: () => void;
}>) {
  const [action, setAction] = useState<Action>(value ?? blankAction("noop"));
  const [issue, setIssue] = useState<string | null>(null);
  const submit = () => {
    // Like the Rules tab: an untouched action is saved as it was; a changed one must be complete.
    const changed = JSON.stringify(action) !== JSON.stringify(value);
    const found = changed ? actionProblem(action) : null;
    setIssue(found);
    if (found) return;
    onSave(changed ? withoutEmptyName(action) : (value as Action));
  };
  return (
    <SharedForm
      label={label}
      scope={scope}
      busy={busy}
      onSubmit={submit}
      onCancel={onCancel}
      extra={onRemove ? (
        <button type="button" className="btn" disabled={busy} onClick={onRemove}>
          Remove for {scope}
        </button>
      ) : null}
    >
      <ActionPicker value={action} actors={actors} triggerType={triggerType} workflow={workflow} onChange={setAction} />
      {issue ? <GuidedNotice code={issue} /> : null}
    </SharedForm>
  );
}

/**
 * "Runs": the run key and the attempt budget. Only the fields the author changed are sent, so
 * an untouched field keeps every rule's own value (its overrides included); an edit the server
 * would refuse for one of `rules` (validate.py: a rule outside the budget) is named, not sent.
 */
export function RunsForm({
  label = "Runs",
  scope = "every entry point",
  rules,
  runKey,
  attempts,
  busy,
  onSave,
  onCancel,
}: Readonly<{
  label?: string;
  scope?: Scope;
  /** The rules the edit writes, for the server's rules on budgets. */
  rules: readonly Rule[];
  runKey: unknown;
  attempts: unknown;
  busy: boolean;
  onSave: (edit: RunsEdit) => void;
  onCancel: () => void;
}>) {
  const initialKey = typeof runKey === "string" ? runKey : "";
  const initialLimit = typeof attempts === "number" ? String(attempts) : "";
  const [key, setKey] = useState(initialKey);
  const [limit, setLimit] = useState(initialLimit);
  const [problem, setProblem] = useState<string | null>(null);
  const submit = () => {
    const edit: RunsEdit = {};
    if (key.trim() !== initialKey) edit.concurrency_key = key.trim() || null;
    if (limit.trim() !== initialLimit) edit.max_attempts = limit.trim() ? Number(limit) : null;
    if (Object.keys(edit).length === 0) return onCancel();
    const found = runsProblem(rules, edit);
    setProblem(found);
    if (!found) onSave(edit);
  };
  return (
    <SharedForm label={label} scope={scope} busy={busy} onCancel={onCancel} onSubmit={submit}>
      <label>
        <span>Run key</span>
        <input value={key} placeholder="No run key" onChange={(e) => setKey(e.target.value)} />
      </label>
      <label>
        <span>Attempts per key</span>
        <input type="number" min={1} step={1} value={limit} placeholder="No limit" onChange={(e) => setLimit(e.target.value)} />
      </label>
      {problem ? (
        <p className="notice notice--error" role="alert">
          {problem}
        </p>
      ) : null}
    </SharedForm>
  );
}

const KEEP = "__keep__";

/** "evaluates on …": one placement for every entry point (a machine, anywhere, or a kept actor/capability one). */
export function PlacementForm({
  value,
  machines,
  busy,
  onSave,
  onCancel,
}: Readonly<{
  value: Placement | null | undefined;
  machines: Machine[];
  busy: boolean;
  onSave: (placement: Placement | null) => void;
  onCancel: () => void;
}>) {
  const placed = value?.machine ?? "";
  const foreign = !placed && (value?.actor || value?.requirement?.length);
  const [choice, setChoice] = useState(foreign ? KEEP : placed);
  const chosen = (): Placement | null => {
    if (choice === KEEP) return value ?? null;
    return choice ? { machine: choice } : null;
  };
  return (
    <SharedForm label="Placement" busy={busy} onCancel={onCancel} onSubmit={() => onSave(chosen())}>
      <label>
        <span>Evaluates on</span>
        <select value={choice} onChange={(e) => setChoice(e.target.value)}>
          <option value="">Anywhere</option>
          {foreign ? <option value={KEEP}>{value?.actor ? `via ${value.actor}` : "by capability"}</option> : null}
          {machines.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
            </option>
          ))}
        </select>
      </label>
    </SharedForm>
  );
}
