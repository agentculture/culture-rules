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
  mixed = false,
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
  /** The entry points hold different actions: nothing is written until one is picked. */
  mixed?: boolean;
}>) {
  const [action, setAction] = useState<Action>((!mixed && value) || blankAction("noop"));
  const [touched, setTouched] = useState(false);
  const [issue, setIssue] = useState<string | null>(null);
  const submit = () => {
    // A differing or absent value is never replaced by the picker's made-up starting point.
    if ((mixed || !value) && !touched) return onCancel();
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
      {mixed ? <p className="fold-entry__meta">Differs per entry point. Pick what happens to set it for {scope}.</p> : null}
      <ActionPicker
        value={action}
        actors={actors}
        triggerType={triggerType}
        workflow={workflow}
        onChange={(next) => {
          setAction(next);
          setTouched(true);
        }}
      />
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
  keyMixed = false,
  attemptsMixed = false,
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
  /** The entry points hold different run keys / budgets: the field starts empty and untouched. */
  keyMixed?: boolean;
  attemptsMixed?: boolean;
  busy: boolean;
  onSave: (edit: RunsEdit) => void;
  onCancel: () => void;
}>) {
  const initialKey = !keyMixed && typeof runKey === "string" ? runKey : "";
  const initialLimit = !attemptsMixed && typeof attempts === "number" ? String(attempts) : "";
  const [key, setKey] = useState(initialKey);
  const [limit, setLimit] = useState(initialLimit);
  // Mixed and untouched sends nothing (each rule keeps its own); touched — typed, or cleared on
  // purpose — sends the field, an empty one as null (no key, no limit) for every rule.
  const [keyTouched, setKeyTouched] = useState(false);
  const [limitTouched, setLimitTouched] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const submit = () => {
    const edit: RunsEdit = {};
    if (keyMixed ? keyTouched : key.trim() !== initialKey) edit.concurrency_key = key.trim() || null;
    if (attemptsMixed ? limitTouched : limit.trim() !== initialLimit) {
      // Junk is refused, never read as "no limit".
      if (limit.trim() && !/^\d+$/.test(limit.trim())) return setProblem("Attempts per key is a whole number, at least 1 (or empty for no limit).");
      edit.max_attempts = limit.trim() ? Number(limit.trim()) : null;
    }
    if (Object.keys(edit).length === 0) return onCancel();
    const found = runsProblem(rules, edit);
    setProblem(found);
    if (!found) onSave(edit);
  };
  const mixedHint = "Differs per entry point";
  return (
    <SharedForm label={label} scope={scope} busy={busy} onCancel={onCancel} onSubmit={submit}>
      <label>
        <span>Run key</span>
        <input
          value={key}
          placeholder={keyMixed && !keyTouched ? mixedHint : "No run key"}
          onChange={(e) => {
            setKey(e.target.value);
            setKeyTouched(true);
          }}
        />
      </label>
      {keyMixed ? (
        <button type="button" className="btn" aria-pressed={keyTouched && !key} onClick={() => { setKey(""); setKeyTouched(true); }}>
          No run key for {scope}
        </button>
      ) : null}
      <label>
        <span>Attempts per key</span>
        <input
          inputMode="numeric"
          value={limit}
          placeholder={attemptsMixed && !limitTouched ? mixedHint : "No limit"}
          onChange={(e) => {
            setLimit(e.target.value);
            setLimitTouched(true);
          }}
        />
      </label>
      {attemptsMixed ? (
        <button type="button" className="btn" aria-pressed={limitTouched && !limit} onClick={() => { setLimit(""); setLimitTouched(true); }}>
          No limit for {scope}
        </button>
      ) : null}
      {problem ? (
        <p className="notice notice--error" role="alert">
          {problem}
        </p>
      ) : null}
    </SharedForm>
  );
}

const KEEP = "__keep__";
const MIXED = "__mixed__";

/** "evaluates on …": one placement for every entry point (a machine, anywhere, or a kept actor/capability one). */
/** The placement select's first value: "differs", keep a non-machine placement, or the machine. */
function initialPlacementChoice(mixed: boolean, foreign: boolean, placed: string): string {
  if (mixed) return MIXED;
  return foreign ? KEEP : placed;
}

export function PlacementForm({
  value,
  mixed = false,
  machines,
  busy,
  onSave,
  onCancel,
}: Readonly<{
  value: Placement | null | undefined;
  /** The entry points evaluate in different places: nothing is written until one is picked. */
  mixed?: boolean;
  machines: Machine[];
  busy: boolean;
  onSave: (placement: Placement | null) => void;
  onCancel: () => void;
}>) {
  const placed = value?.machine ?? "";
  const foreign = !placed && (value?.actor || value?.requirement?.length);
  const [choice, setChoice] = useState(() => initialPlacementChoice(mixed, Boolean(foreign), placed));
  const chosen = (): Placement | null => {
    if (choice === KEEP) return value ?? null;
    return choice ? { machine: choice } : null;
  };
  return (
    <SharedForm label="Placement" busy={busy} onCancel={onCancel} onSubmit={() => (choice === MIXED ? onCancel() : onSave(chosen()))}>
      <label>
        <span>Evaluates on</span>
        <select value={choice} onChange={(e) => setChoice(e.target.value)}>
          {mixed ? <option value={MIXED}>Differs per entry point</option> : null}
          <option value="">Anywhere</option>
          {!mixed && foreign ? <option value={KEEP}>{value?.actor ? `via ${value.actor}` : "by capability"}</option> : null}
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
