/**
 * The Simple view (spec c13, h10): a workflow as text, no canvas and no cards.
 *
 *   - the workflow's own description (editable in place; a visible gap when empty);
 *   - "How it runs": the paragraph from GET /workflows/{id}/describe, rendered from
 *     its segments (start sentences, the steps, the Then sentence) with the fixed
 *     phrases highlighted and a step with no description shown as a dashed gap that
 *     names it. Nothing is composed here (o4): see ./paragraphText.ts.
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
import { putWorkflowDef, type Step, type WorkflowDef } from "../../api/workflows";
import { toDefinition } from "../model";
import { useFeedSubscription, type LiveFeed } from "./liveFeed";
import { stepViews, type StepView } from "./paragraphText";
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

type Target = { kind: "workflow" } | { kind: "step"; id: string } | null;

/** Text that turns into a field when clicked; `placeholder` is the visible gap when it is empty. */
function EditableText({
  value,
  gapText,
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
  /** What shows when `value` is empty: the dashed gap. */
  gapText: string;
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
      <button
        type="button"
        className={`sv-text ${value ? "" : "sv-gap"} ${className ?? ""}`.trim()}
        title={label}
        disabled={busy}
        onClick={onEdit}
      >
        {value || gapText}
      </button>
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

function Sentences({ segments }: Readonly<{ segments: ParagraphSegment[] }>) {
  if (segments.length === 0) return null;
  return (
    <p className="sv-p">
      {segments.map((seg, i) => (
        <span key={i}>
          {i > 0 ? " " : ""}
          <span className={seg.disabled ? "sv-phrase sv-disabled" : "sv-phrase"}>{seg.text}</span>
          {seg.disabled ? <span className="sv-sr"> (disabled)</span> : null}
        </span>
      ))}{" "}
    </p>
  );
}

function StepSentence({
  view,
  index,
  target,
  busy,
  onEdit,
  onSave,
  onCancel,
}: Readonly<{
  view: StepView;
  index: number;
  target: Target;
  busy: boolean;
  onEdit: (id: string) => void;
  onSave: (id: string, next: string) => void;
  onCancel: () => void;
}>) {
  const { seg } = view;
  const id = String(seg.step ?? "");
  const phrase = typeof seg.phrase === "string" ? seg.phrase : "";
  const name = String(seg.name ?? id);
  const editing = target?.kind === "step" && target.id === id;
  const sep = index > 0 && !view.joined ? " " : "";
  return (
    <span className={seg.disabled ? "sv-step sv-disabled" : "sv-step"} data-step={id}>
      {seg.disabled ? <span className="sv-sr">Disabled step: </span> : null}
      {sep}
      {phrase ? <span className="sv-phrase">{phrase}</span> : null}
      {phrase ? " " : ""}
      {view.fallback ? (
        <span className="sv-fallback">{view.shown}</span>
      ) : (
        <EditableText
          value={editing ? view.description : view.gap ? "" : view.shown}
          gapText={view.shown}
          label={`Edit the description of ${name}`}
          editing={editing}
          busy={busy}
          onEdit={() => onEdit(id)}
          onSave={(next) => onSave(id, next)}
          onCancel={onCancel}
        />
      )}
    </span>
  );
}

function Steps({
  segments,
  ...rest
}: Readonly<{
  segments: ParagraphSegment[];
  target: Target;
  busy: boolean;
  onEdit: (id: string) => void;
  onSave: (id: string, next: string) => void;
  onCancel: () => void;
}>) {
  const views = useMemo(() => stepViews(segments), [segments]);
  if (views.length === 0) return null;
  // A loop's body (depth 1 and deeper) reads as an indented block under its loop.
  const out: React.ReactNode[] = [];
  let body: React.ReactNode[] = [];
  const flush = (key: number) => {
    if (body.length === 0) return;
    out.push(
      <span key={`body-${key}`} className="sv-body">
        {body}
      </span>,
    );
    body = [];
  };
  views.forEach((view, i) => {
    const sentence = <StepSentence key={i} view={view} index={i} {...rest} />;
    if (Number(view.seg.depth ?? 0) > 0) {
      body.push(
        <span key={i} className="sv-body__line">
          {sentence}{" "}
        </span>,
      );
    } else {
      flush(i);
      out.push(sentence);
    }
  });
  flush(views.length);
  return <p className="sv-p">{out}{" "}</p>;
}

export function SimpleView({ def, live, onSaved }: Readonly<SimpleViewProps>) {
  const workflowId = def.id;
  const [paragraph, setParagraph] = useState<Paragraph | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [target, setTarget] = useState<Target>(null);
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

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
    try {
      const saved = await putWorkflowDef(toDefinition(next));
      setTarget(null);
      onSaved?.(saved);
      setTick((n) => n + 1);
    } catch (err) {
      setSaveError(`Save failed: ${errorMessage(err)}`);
      setTarget(null);
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
  const starts = segments.filter((s) => s.part === "start");
  const steps = segments.filter((s) => s.part === "step");
  const thens = segments.filter((s) => s.part === "then");

  return (
    <section className="sv" aria-label="Simple view">
      {saveError ? (
        <p className="notice notice--error" role="alert">
          {saveError}
        </p>
      ) : null}
      <div className="sv-description" aria-label="Workflow description">
        <EditableText
          value={def.description?.trim() ?? ""}
          gapText="No description yet"
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
          <Sentences segments={starts} />
          <Steps
            segments={steps}
            target={target}
            busy={busy}
            onEdit={(id) => setTarget({ kind: "step", id })}
            onSave={saveStep}
            onCancel={() => setTarget(null)}
          />
          <Sentences segments={thens} />
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
