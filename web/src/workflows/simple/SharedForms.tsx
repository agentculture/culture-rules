import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import type { Actor } from "../../api/actors";
import type { ApiError } from "../../api/client";
import type { Action, Machine, Placement, Rule, Workflow } from "../../api/types";
import GuidedNotice from "../../components/GuidedNotice";
import { useEscapeKey } from "../../hooks/useEscapeKey";
import ActionPicker, { actionProblem, blankAction } from "../../rules/ActionPicker";
import { groupRanking, parsePriority, priorityOf, runsProblem, type GroupMember, type RunsEdit } from "./text";

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

export type GroupField = "exclusive_group" | "priority";
export type GroupEdit = Partial<{ exclusive_group: string | null; priority: number }>;
export type GroupProblems = Partial<Record<GroupField, string>>;

const GROUP_FIELDS: readonly GroupField[] = ["exclusive_group", "priority"];

/** The rule field a 422 path names at the top level (a request-body `body.` prefix dropped). */
function topField(path: string): string | undefined {
  const tokens = path.split(/[.[\]/]/).filter(Boolean);
  return tokens[0] === "body" ? tokens[1] : tokens[0];
}

/**
 * A 422's messages, each at the field its path names; one naming neither field (or a bare
 * envelope) is shown at the first field the edit changed.
 */
export function groupProblems(err: ApiError, edit: GroupEdit): GroupProblems {
  const found: GroupProblems = {};
  for (const e of err.errors) {
    const field = GROUP_FIELDS.find((f) => topField(e.path) === f);
    if (field) found[field] = found[field] ? `${found[field]}; ${e.message}` : e.message;
  }
  if (Object.keys(found).length > 0) return found;
  const first = GROUP_FIELDS.find((f) => f in edit) ?? "exclusive_group";
  return { [first]: err.message };
}

/** The rules sharing `group`, this one at its typed priority, ranked as the engine picks. */
function GroupMembers({ rule, group, priority, rules }: Readonly<{ rule: Rule; group: string; priority: number; rules: readonly Rule[] }>) {
  if (!group) return null;
  const others: GroupMember[] = rules
    .filter((r) => r.id !== rule.id && r.exclusive_group === group)
    .map((r) => ({ id: r.id, name: r.name, priority: priorityOf(r), enabled: r.enabled !== false }));
  if (others.length === 0) return <p className="fold-entry__meta">No other rule is in group {group}.</p>;
  const self = { id: rule.id, name: rule.name, priority, enabled: rule.enabled !== false };
  const { ranked, winner, tied } = groupRanking([self, ...others]);
  return (
    <>
      <ul className="fold-group" aria-label={`Rules in group ${group}`}>
        {ranked.map((m) => (
          <li key={m.id} className="fold-group__member">
            <span className="fold-group__name">{m.name}</span>
            <span className="fold-entry__meta">priority {m.priority}</span>
            {m === self ? <span className="fold-entry__meta">(this one)</span> : null}
            {m === winner ? <span className="fold-badge fold-badge--win">wins</span> : null}
            {m.enabled ? null : <span className="fold-badge fold-badge--off">disabled</span>}
          </li>
        ))}
      </ul>
      {tied && winner ? (
        <p className="fold-entry__meta">Tie at priority {winner.priority}: {winner.id} wins, first by id.</p>
      ) : null}
    </>
  );
}

/**
 * "Group and priority": the rule's exclusive group and its priority in it, edited together.
 * Only the fields the author changed are sent; an emptied group is sent as null (validate.py
 * refuses an empty one), a priority must be a whole number. `onSave` answers the server's
 * refusal per field (a 422), shown at that field with the form kept open.
 */
export function GroupForm({
  label,
  rule,
  rules,
  busy,
  onSave,
  onCancel,
}: Readonly<{
  label: string;
  /** The rule as it was when the form opened (c27). */
  rule: Rule;
  /** Every rule, for the group's other members. */
  rules: readonly Rule[];
  busy: boolean;
  onSave: (edit: GroupEdit) => Promise<GroupProblems | null>;
  onCancel: () => void;
}>) {
  const initialGroup = typeof rule.exclusive_group === "string" ? rule.exclusive_group : "";
  const initialPriority = priorityOf(rule);
  const [group, setGroup] = useState(initialGroup);
  const [priority, setPriority] = useState(String(initialPriority));
  const [problems, setProblems] = useState<GroupProblems>({});
  const id = useId();
  const typed = parsePriority(priority);
  const submit = async () => {
    if (typed === null) return setProblems({ priority: "Priority is a whole number; higher wins (empty is 0)." });
    const edit: GroupEdit = {};
    if (group.trim() !== initialGroup) edit.exclusive_group = group.trim() || null;
    if (typed !== initialPriority) edit.priority = typed;
    if (Object.keys(edit).length === 0) return onCancel();
    setProblems({});
    const found = await onSave(edit);
    if (found) setProblems(found);
  };
  const described = (field: GroupField) => ({
    "aria-invalid": problems[field] ? true : undefined,
    "aria-describedby": problems[field] ? `${id}-${field}` : undefined,
  });
  const problem = (field: GroupField) =>
    problems[field] ? (
      <p id={`${id}-${field}`} className="notice notice--error fold-field-problem" role="alert">
        {problems[field]}
      </p>
    ) : null;
  return (
    <SharedForm label={label} scope="this entry point" busy={busy} onCancel={onCancel} onSubmit={() => void submit()}>
      <label>
        <span>Group</span>
        <input value={group} placeholder="No group" {...described("exclusive_group")} onChange={(e) => setGroup(e.target.value)} />
      </label>
      {problem("exclusive_group")}
      <label>
        <span>Priority</span>
        <input inputMode="numeric" value={priority} placeholder="0" {...described("priority")} onChange={(e) => setPriority(e.target.value)} />
      </label>
      {problem("priority")}
      <GroupMembers rule={rule} group={group.trim()} priority={typed ?? initialPriority} rules={rules} />
    </SharedForm>
  );
}
