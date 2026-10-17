/**
 * The Simple view (spec c13, h10): a workflow as text, no canvas and no cards.
 *
 *   - the workflow's own description (editable in place; a visible gap when empty);
 *   - "How it runs": the paragraph from GET /workflows/{id}/describe, rendered from
 *     its segments, one line per sentence (d11), with the fixed phrases highlighted and
 *     a step with no description shown as its stand-in, italic and marked not written
 *     yet. Nothing is composed here (o4): a segment reads `phrase shown tail`.
 *
 * A step's one-line description (and the workflow's) is edited in place: click the
 * text, type, Enter or leave the field to save, Escape to cancel. A save is the
 * existing workflow save (PUT /workflows/{id}); the paragraph is read again after it,
 * and after a rule or workflow change reaches the page's live feed.
 *
 * The entry points and the Then side (the old When / Then board) live in Detailed
 * (../views/EntryEditors.tsx); a rule without a workflow is offered its D7 workflow
 * in the page's own place (routes/Workflows.tsx).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getDescription } from "../../api/describe";
import type { Paragraph, ParagraphSegment } from "../../api/types";
import type { LiveChange } from "../../api/live";
import {
  REVISION_CONFLICT_MESSAGE,
  getWorkflowDef,
  isRevisionConflict,
  putWorkflowDef,
  type Step,
  type WorkflowDef,
} from "../../api/workflows";
import { toDefinition } from "../model";
import { useFeedSubscription, type LiveFeed } from "./liveFeed";
import "./simple-text.css";

export interface SimpleViewProps {
  /** The stored workflow (its saved copy): the description and the steps' descriptions are edited on it. */
  def: WorkflowDef;
  /** Live changes from the page's own stream: a rule or workflow change reads the paragraph again. */
  live?: LiveFeed;
  /** A save stored this definition (the page replaces its copy). */
  onSaved?: (saved: WorkflowDef) => void;
}

const errorMessage = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** `steps` with one step's description replaced, in its loop bodies too. */
function withStepDescription(steps: readonly Step[], id: string, description: string): Step[] {
  return steps.map((s) => {
    if (s.id === id) {
      const { description: _old, ...rest } = s;
      return description ? { ...rest, description } : rest;
    }
    return s.body ? { ...s, body: withStepDescription(s.body, id, description) } : s;
  });
}

/** The same hint the Detailed editor gives (d11). */
const HINT = "Start with a verb, one line: 'Packs the commits for the hand-off.'";

type Target = { kind: "workflow" } | { kind: "step"; id: string } | null;

/** Text that turns into a field when clicked; `placeholder` is the visible gap when it is empty. */
function EditableText({
  value,
  display,
  standin = false,
  label,
  multiline = false,
  editing,
  busy,
  className,
  onEdit,
  onSave,
  onCancel,
}: Readonly<{
  value: string;
  /** What shows when not editing: `value` or, when that is empty, this (the gap or stand-in). */
  display: string;
  /** The words are the API's stand-in for a missing description: italic, muted, cued. */
  standin?: boolean;
  label: string;
  multiline?: boolean;
  editing: boolean;
  busy: boolean;
  className?: string;
  onEdit: () => void;
  onSave: (next: string) => void;
  onCancel: () => void;
}>) {
  const [draft, setDraft] = useState(value);
  const field = useRef<HTMLInputElement & HTMLTextAreaElement>(null);
  const done = useRef(false);
  useEffect(() => {
    if (!editing) return;
    done.current = false;
    setDraft(value);
    field.current?.focus();
    // value is read when the field opens only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing]);
  if (!editing) {
    return (
      // A span, not a <button>: a button is an atomic inline box and would not wrap with the text.
      <span
        role="button"
        tabIndex={busy ? -1 : 0}
        aria-disabled={busy || undefined}
        className={`sv-text ${standin ? "sv-standin" : value ? "" : "sv-gap"} ${className ?? ""}`.trim()}
        title={label}
        onClick={() => {
          if (!busy) onEdit();
        }}
        onKeyDown={(e) => {
          if (busy || (e.key !== "Enter" && e.key !== " ")) return;
          e.preventDefault();
          onEdit();
        }}
      >
        {display}
        {standin ? <span className="sv-sr"> (not written yet)</span> : null}
      </span>
    );
  }
  const finish = (save: boolean) => {
    if (done.current) return;
    done.current = true;
    if (save && draft.trim() !== value) onSave(draft.trim());
    else onCancel();
  };
  const common = {
    ref: field,
    className: `sv-field ${className ?? ""}`.trim(),
    "aria-label": label,
    placeholder: HINT,
    value: draft,
    onChange: (e: { target: { value: string } }) => setDraft(e.target.value),
    onBlur: () => finish(true),
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === "Escape") finish(false);
      else if (e.key === "Enter" && (!multiline || e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        finish(true);
      }
    },
  };
  return multiline ? <textarea rows={3} {...common} /> : <input type="text" {...common} />;
}

const str = (v: unknown) => (typeof v === "string" ? v : "");

type StepEditing = Readonly<{
  target: Target;
  busy: boolean;
  onEdit: (id: string) => void;
  onSave: (id: string, next: string) => void;
  onCancel: () => void;
}>;

/** One step's words in its sentence: `phrase shown tail`, nothing composed here (o4). */
function StepPart({ seg, ...rest }: Readonly<{ seg: ParagraphSegment } & StepEditing>) {
  const id = str(seg.step);
  const phrase = str(seg.phrase);
  const shown = str(seg.shown) || str(seg.text);
  const tail = str(seg.tail);
  const name = str(seg.name) || id;
  const standin = seg.standin === true;
  const editing = rest.target?.kind === "step" && rest.target.id === id;
  return (
    <span className={seg.disabled ? "sv-step sv-disabled" : "sv-step"} data-step={id}>
      {seg.disabled ? <span className="sv-sr">Disabled step: </span> : null}
      {phrase ? <span className="sv-phrase">{phrase}</span> : null}
      {phrase ? " " : ""}
      {seg.fallback === true ? (
        <span className="sv-fallback">{shown}</span>
      ) : (
        <EditableText
          value={standin ? "" : str(seg.text)}
          display={shown}
          standin={standin}
          label={`Edit the description of ${name}`}
          editing={editing}
          busy={rest.busy}
          onEdit={() => rest.onEdit(id)}
          onSave={(next) => rest.onSave(id, next)}
          onCancel={rest.onCancel}
        />
      )}
      {tail}
    </span>
  );
}

/** The paragraph, one line per sentence (d11): the API's segments, grouped by `sentence`. */
function Lines({ segments, ...rest }: Readonly<{ segments: ParagraphSegment[] } & StepEditing>) {
  const lines: ParagraphSegment[][] = [];
  let last: unknown;
  segments.forEach((seg, i) => {
    const key = seg.sentence ?? `own-${i}`;
    if (lines.length === 0 || key !== last) lines.push([]);
    lines[lines.length - 1].push(seg);
    last = key;
  });
  return (
    <>
      {lines.map((line, i) => {
        const depth = Number(line[0].depth ?? 0);
        return (
          <p key={i} className={depth > 0 ? "sv-line sv-line--in" : "sv-line"}>
            {line.map((seg, j) =>
              seg.part === "step" ? (
                <StepPart key={j} seg={seg} {...rest} />
              ) : (
                <span key={j} className={seg.disabled ? "sv-text-part sv-disabled" : "sv-text-part"}>
                  {seg.disabled ? <span className="sv-sr">Disabled: </span> : null}
                  {seg.text}
                </span>
              ),
            )}
            {"\n"}
          </p>
        );
      })}
    </>
  );
}

export function SimpleView({ def, live, onSaved }: Readonly<SimpleViewProps>) {
  const workflowId = def.id;
  const [paragraph, setParagraph] = useState<Paragraph | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [target, setTarget] = useState<Target>(null);
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  // A save refused because the workflow changed meanwhile: the typed text stays in its field.
  const [conflict, setConflict] = useState(false);

  // Read again when the stored definition changes (a save, a reload) or the live feed says a
  // rule or workflow did.
  const stored = useMemo(() => JSON.stringify(toDefinition(def)), [def]);
  useEffect(() => {
    const ctl = new AbortController();
    getDescription("workflows", workflowId, ctl.signal)
      .then((d) => {
        setParagraph(d.paragraph ?? null);
        setProblem(d.paragraph ? null : "The API sent no paragraph for this workflow.");
      })
      .catch((err) => {
        if (!ctl.signal.aborted) setProblem(`Could not read how this workflow runs: ${errorMessage(err)}`);
      });
    return () => ctl.abort();
  }, [workflowId, stored, tick]);
  const onLive = useCallback((changes: LiveChange[]) => {
    if (changes.some((c) => c.collection === "rules" || c.collection === "workflows")) setTick((n) => n + 1);
  }, []);
  useFeedSubscription(live, onLive);

  const save = async (next: WorkflowDef) => {
    setBusy(true);
    setSaveError(null);
    setConflict(false);
    try {
      const saved = await putWorkflowDef(toDefinition(next), def.revision ?? 0);
      setTarget(null);
      onSaved?.(saved);
      setTick((n) => n + 1);
    } catch (err) {
      if (isRevisionConflict(err)) {
        setConflict(true);
      } else {
        setSaveError(`Save failed: ${errorMessage(err)}`);
        setTarget(null);
      }
    } finally {
      setBusy(false);
    }
  };
  // Reload replaces the stored copy (and its revision); only now is the typed text let go.
  const reload = async () => {
    setBusy(true);
    try {
      onSaved?.(await getWorkflowDef(workflowId));
      setConflict(false);
      setTarget(null);
      setTick((n) => n + 1);
    } catch (err) {
      setSaveError(`Reload failed: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  };
  const saveStep = (id: string, description: string) =>
    void save({ ...def, steps: withStepDescription(def.steps ?? [], id, description) });
  const saveWorkflow = (description: string) => {
    const { description: _old, ...rest } = def;
    void save(description ? { ...rest, description } : rest);
  };

  const segments = paragraph?.segments ?? [];

  return (
    <section className="sv" aria-label="Simple view">
      {conflict ? (
        <p className="notice notice--error" role="alert">
          {REVISION_CONFLICT_MESSAGE}.{" "}
          <button type="button" className="wf-button" disabled={busy} onClick={() => void reload()}>
            Reload
          </button>
        </p>
      ) : null}
      {saveError ? (
        <p className="notice notice--error" role="alert">
          {saveError}
        </p>
      ) : null}
      <div className="sv-description" aria-label="Workflow description">
        <EditableText
          value={def.description?.trim() ?? ""}
          display={def.description?.trim() || "No description yet"}
          label="Edit the description of this workflow"
          multiline
          className="sv-lede"
          editing={target?.kind === "workflow"}
          busy={busy}
          onEdit={() => setTarget({ kind: "workflow" })}
          onSave={saveWorkflow}
          onCancel={() => setTarget(null)}
        />
      </div>
      <h2 className="sv-heading">How it runs</h2>
      {problem ? (
        <p className="notice notice--error" role="alert">
          {problem}
        </p>
      ) : null}
      {paragraph ? (
        <div className="sv-para" data-testid="paragraph">
          <Lines
            segments={segments}
            target={target}
            busy={busy}
            onEdit={(id) => setTarget({ kind: "step", id })}
            onSave={saveStep}
            onCancel={() => setTarget(null)}
          />
        </div>
      ) : problem ? null : (
        <p className="sv-loading" role="status">
          Reading how it runs…
        </p>
      )}
    </section>
  );
}

export default SimpleView;
