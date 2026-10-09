import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import type { Actor } from "../../api/actors";
import type { Action, Machine, Placement, Workflow } from "../../api/types";
import GuidedNotice from "../../components/GuidedNotice";
import { useEscapeKey } from "../../hooks/useEscapeKey";
import ActionPicker, { actionProblem, blankAction } from "../../rules/ActionPicker";

/**
 * The workflow-level forms of the Simple view. Each edits one value every
 * entry point holds (D3-D6) and hands it to the fold writes' fan-out, which
 * writes every entry point's rule one at a time. Escape cancels, focus lands
 * in the first field, like the Rules tab's forms.
 */
function SharedForm({
  label,
  onSubmit,
  onCancel,
  busy,
  extra,
  children,
}: Readonly<{ label: string; onSubmit: () => void; onCancel: () => void; busy: boolean; extra?: ReactNode; children: ReactNode }>) {
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
          Save for every entry point
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
      busy={busy}
      onSubmit={submit}
      onCancel={onCancel}
      extra={onRemove ? (
        <button type="button" className="btn" disabled={busy} onClick={onRemove}>
          Remove for every entry point
        </button>
      ) : null}
    >
      <ActionPicker value={action} actors={actors} triggerType={triggerType} workflow={workflow} onChange={setAction} />
      {issue ? <GuidedNotice code={issue} /> : null}
    </SharedForm>
  );
}

/** "Runs": the run key and the attempt budget every entry point shares. */
export function RunsForm({
  runKey,
  attempts,
  busy,
  onSave,
  onCancel,
}: Readonly<{
  runKey: unknown;
  attempts: unknown;
  busy: boolean;
  onSave: (edit: { concurrency_key: string | null; max_attempts: number | null }) => void;
  onCancel: () => void;
}>) {
  const [key, setKey] = useState(typeof runKey === "string" ? runKey : "");
  const [limit, setLimit] = useState(typeof attempts === "number" ? String(attempts) : "");
  return (
    <SharedForm
      label="Runs"
      busy={busy}
      onCancel={onCancel}
      onSubmit={() => onSave({ concurrency_key: key.trim() || null, max_attempts: limit ? Number(limit) : null })}
    >
      <label>
        <span>Run key</span>
        <input value={key} placeholder="pr:{trigger.data.repository}#{trigger.data.number}" onChange={(e) => setKey(e.target.value)} />
      </label>
      <label>
        <span>Attempts per key</span>
        <input type="number" min={1} step={1} value={limit} placeholder="No limit" onChange={(e) => setLimit(e.target.value)} />
      </label>
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
