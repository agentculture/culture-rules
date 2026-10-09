import { forwardRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Actor } from "../../api/actors";
import type { Action, Rule, Workflow } from "../../api/types";
import { predecessorTerms, type Continuation } from "../../fold/model";
import { actionChips } from "../../routes/rules-view";
import { useFocusReturn } from "./focus";
import { useFrozen } from "./freeze";
import { RunsForm, SharedActionForm } from "./SharedForms";
import { actionBody, actionText, attemptsText, conditionRows, rowText, runEventWords, split, valueText, type Split } from "./text";

export type Editing = "ends" | "failure" | "runs" | null;

const ContinueIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
    <path d="M4 4v7a4 4 0 0 0 4 4h12M16 11l4 4-4 4" />
  </svg>
);
const CommentIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
    <path d="M4 5h16v11H9l-5 4z" />
  </svg>
);
const BackIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M9 14 4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3" />
  </svg>
);
const ClockIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </svg>
);

function Card({
  label,
  icon,
  tone,
  edit,
  children,
}: Readonly<{ label: string; icon: ReactNode; tone?: "warn"; edit?: ReactNode; children: ReactNode }>) {
  return (
    <div role="group" aria-label={label} className="fold-then">
      <span className={`fold-then__kicker${tone ? ` fold-then__kicker--${tone}` : ""}`}>
        {icon}
        <span className="fold-then__label">{label}</span>
        {edit}
      </span>
      {children}
    </div>
  );
}

const EditButton = forwardRef<HTMLButtonElement, { label: string; open: boolean; onClick: () => void }>(function EditButton(
  { label, open, onClick },
  ref,
) {
  return (
    <button ref={ref} type="button" className="fold-then__edit" aria-label={label} aria-expanded={open} onClick={onClick}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M4 20h4L19 9l-4-4L4 16v4z" />
      </svg>
    </button>
  );
});

/** The workflow's value in words, or that the entry points differ (each then shows its own). */
function shown(s: Split, words: (value: unknown) => string): string {
  return s.shared || s.baseline ? words(s.value) : "Differs per entry point";
}

/** The action's params mapped from a reference, as the Rules board drew them: "image → tag". */
function Mapped({ action }: Readonly<{ action: Action | null | undefined }>) {
  const chips = action ? actionChips(action) : [];
  if (chips.length === 0) return null;
  return (
    <ul className="fold-then__mapped" aria-label="Mapped params">
      {chips.map((chip) => (
        <li key={chip} className="fold-term fold-term--field">{chip}</li>
      ))}
    </ul>
  );
}

/** Entry points whose value differs from the one shown for the workflow: name and own value. */
function Overrides({ items }: Readonly<{ items: { rule: Rule; text: string }[] }>) {
  if (items.length === 0) return null;
  return (
    <ul className="fold-overrides" aria-label="Overrides">
      {items.map(({ rule, text }) => (
        <li key={`${rule.id}-${text}`}>
          <span className="fold-badge fold-badge--override">override</span> {rule.name}: {text}
        </li>
      ))}
    </ul>
  );
}

/** A continuation that starts after this workflow, as a read-only link to the workflow it starts (D2). */
function Onward({ entry, nameOf, hrefFor }: Readonly<{ entry: Continuation; nameOf: (id: string) => string; hrefFor: (id: string) => string }>) {
  const rest = conditionRows(entry.rule.condition, predecessorTerms(entry.rule.condition)).map(rowText);
  return (
    <li className="fold-onward">
      <Link className="fold-onward__link" to={hrefFor(entry.workflowId)}>
        {nameOf(entry.workflowId)} <span className="fold-term fold-term--field">{entry.workflowId}</span>
        <span aria-hidden="true"> ›</span>
      </Link>
      <span className="fold-then__words">
        {entry.predecessor.kind === "any" ? "after any workflow's run " : "when this run "}
        {runEventWords(entry.rule.trigger)}
        {rest.length ? " and " : ""}
        {rest.map((text, i) => (
          <span key={`${i}-${text}`} className="fold-term fold-term--field">{text}</span>
        ))}
        {entry.enabled ? "" : " (disabled)"}
      </span>
      <span className="fold-then__note">Kept on {entry.workflowId} as its entry point.</span>
    </li>
  );
}

export interface ThenColumnProps {
  workflowId: string;
  /** Every rule that starts this workflow (entry points and continuations). */
  rules: Rule[];
  /** Continuations of other workflows that start after this one (linked) or after any. */
  onward: Continuation[];
  workflows: Workflow[];
  actors: Actor[];
  triggerType?: string;
  busy: boolean;
  hrefFor: (workflowId: string) => string;
  /** Fan `edit` out to `snapshots`: the rules as they were when the form opened. */
  onFanOut: (label: string, edit: Record<string, unknown>, snapshots: readonly Rule[]) => void;
  /** Per field, the value the last shared edit wrote: rules that did not take it are the overrides. */
  baselines?: Record<string, { value: unknown }>;
}

/**
 * The Then column (canvas Fold-Editor): where the workflow continues (D2,
 * read-only links; each continuation is edited on the workflow it starts),
 * how it ends (the chain-end action), what runs on failure, and how it runs
 * (the run key and attempt budget). A value every entry point holds shows
 * once; entries that differ are listed as overrides. Editing writes every
 * entry point's rule, one at a time.
 */
export function ThenColumn({ workflowId, rules, onward, workflows, actors, triggerType, busy, hrefFor, onFanOut, baselines = {} }: Readonly<ThenColumnProps>) {
  const [editing, setEditing] = useState<Editing>(null);
  const endsButton = useFocusReturn<HTMLButtonElement>(editing === "ends");
  const failureButton = useFocusReturn<HTMLButtonElement>(editing === "failure");
  const runsButton = useFocusReturn<HTMLButtonElement>(editing === "runs");
  const workflow = workflows.find((w) => w.id === workflowId);
  const nameOf = (id: string) => workflows.find((w) => w.id === id)?.name ?? id;
  const ends = split(rules, "action", baselines.action);
  const failure = split(rules, "on_failure", baselines.on_failure);
  const key = split(rules, "concurrency_key", baselines.concurrency_key);
  const attempts = split(rules, "max_attempts", baselines.max_attempts);
  const counted = rules.filter((r) => (r as unknown as Record<string, unknown>).counts_toward_budget !== false).length;
  const endsAction = ends.value as Action | null | undefined;
  const failAction = failure.value as Action | null | undefined;
  const failDiffers = !failure.shared && !failure.baseline;
  // What the open form was opened on: its values, and the rules its save compares against (c27).
  const at = useFrozen(editing !== null, { rules, ends, failure, key, attempts });
  const save = (label: string, edit: Record<string, unknown>) => {
    setEditing(null);
    onFanOut(label, edit, at.rules);
  };
  const toggle = (which: Editing) => () => setEditing((e) => (e === which ? null : which));
  const noEntries = rules.length === 0;

  return (
    <section className="fold-col fold-col--then" aria-labelledby={`${workflowId}-then`}>
      <h2 id={`${workflowId}-then`} className="fold-col__title">Then</h2>

      <Card label="Continues into" icon={<ContinueIcon />}>
        {onward.length === 0 ? (
          <span className="fold-then__words">Nothing continues from here.</span>
        ) : (
          <ul className="fold-onwards">
            {onward.map((entry) => (
              <Onward key={entry.rule.id} entry={entry} nameOf={nameOf} hrefFor={hrefFor} />
            ))}
          </ul>
        )}
      </Card>

      <Card label="Ends here" icon={<CommentIcon />} edit={noEntries ? null : <EditButton ref={endsButton} label="Edit ends here" open={editing === "ends"} onClick={toggle("ends")} />}>
        <span className="fold-then__value">{noEntries ? "No entry point starts it yet." : shown(ends, (v) => actionText(v as Action | null | undefined))}</span>
        {actionBody(endsAction) ? <span className="fold-then__body">{actionBody(endsAction)}</span> : null}
        <Mapped action={noEntries || !(ends.shared || ends.baseline) ? null : endsAction} />
        {noEntries || !(ends.shared || ends.baseline) ? null : (
          <span className="fold-then__note">
            {endsAction?.only_at_chain_end ? "Only when the chain ends here." : "After every run."}
          </span>
        )}
        <Overrides items={ends.overrides.map((o) => ({ rule: o.rule, text: valueText("action", o.value) }))} />
        {editing === "ends" ? (
          <SharedActionForm
            label="Ends here"
            value={at.ends.value as Action | null | undefined}
            mixed={!at.ends.shared && !at.ends.baseline}
            actors={actors}
            triggerType={triggerType}
            workflow={workflow}
            busy={busy}
            onSave={(action) => save("Ends here", { action })}
            onCancel={() => setEditing(null)}
          />
        ) : null}
      </Card>

      <Card label="On failure" icon={<BackIcon />} tone="warn" edit={noEntries ? null : <EditButton ref={failureButton} label="Edit on failure" open={editing === "failure"} onClick={toggle("failure")} />}>
        <span className="fold-then__value">
          {failDiffers ? "Differs per entry point" : failAction ? actionText(failAction) : "Nothing runs on failure."}
        </span>
        {actionBody(failAction) ? <span className="fold-then__body">{actionBody(failAction)}</span> : null}
        <Mapped action={failDiffers ? null : failAction} />
        {failAction?.only_at_chain_end ? <span className="fold-then__note">Once per chain.</span> : null}
        <Overrides items={failure.overrides.map((o) => ({ rule: o.rule, text: valueText("on_failure", o.value) }))} />
        {editing === "failure" ? (
          <SharedActionForm
            label="On failure"
            value={at.failure.value as Action | null | undefined}
            mixed={!at.failure.shared && !at.failure.baseline}
            actors={actors}
            triggerType={triggerType}
            workflow={workflow}
            busy={busy}
            onSave={(action) => save("On failure", { on_failure: action })}
            onRemove={at.rules.some((r) => r.on_failure) ? () => save("On failure", { on_failure: null }) : undefined}
            onCancel={() => setEditing(null)}
          />
        ) : null}
      </Card>

      <Card label="Runs" icon={<ClockIcon />} edit={noEntries ? null : <EditButton ref={runsButton} label="Edit runs" open={editing === "runs"} onClick={toggle("runs")} />}>
        <span className="fold-then__value">
          {!key.shared && !key.baseline ? "Run key differs per entry point"
            : typeof key.value === "string" && key.value ? "One at a time per key" : "Runs side by side"}
        </span>
        {(key.shared || key.baseline) && typeof key.value === "string" && key.value ? <span className="fold-then__key">{key.value}</span> : null}
        <span className="fold-then__words">
          {shown(attempts, attemptsText)}
          {(attempts.shared || attempts.baseline) && typeof attempts.value === "number" ? `, counted by ${counted} of ${rules.length} entry points` : ""}
        </span>
        <Overrides
          items={[
            ...key.overrides.map((o) => ({ rule: o.rule, text: valueText("concurrency_key", o.value) })),
            ...attempts.overrides.map((o) => ({ rule: o.rule, text: valueText("max_attempts", o.value) })),
          ]}
        />
        {editing === "runs" ? (
          <RunsForm
            rules={at.rules}
            runKey={at.key.value}
            keyMixed={!at.key.shared && !at.key.baseline}
            attemptsMixed={!at.attempts.shared && !at.attempts.baseline}
            attempts={at.attempts.value}
            busy={busy}
            onSave={(edit) => save("Runs", { ...edit })}
            onCancel={() => setEditing(null)}
          />
        ) : null}
      </Card>
    </section>
  );
}
