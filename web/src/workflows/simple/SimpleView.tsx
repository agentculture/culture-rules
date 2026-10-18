/**
 * The Simple view (spec c13, h10): a workflow as text, no canvas and no cards.
 *
 *   - the workflow's own description (editable in place; a visible gap when empty);
 *   - "How it runs": the paragraph from GET /workflows/{id}/describe, rendered from
 *     its segments, one line per sentence (d11), with the fixed phrases highlighted and
 *     a step with no description shown as its stand-in, italic and marked not written
 *     yet. Nothing is composed here (o4): a segment reads `phrase shown tail`.
 *
 * The workflow's own description is edited in place: click the text, type, Enter or leave
 * the field to save, Escape to cancel. A step's text opens the same side panel Detailed opens
 * for it (d14), and a rule's sentence opens that rule's editor; both are mounted by the page
 * (routes/Workflows.tsx). A save is the existing workflow save (PUT /workflows/{id}); the
 * paragraph is read again after it, and after a rule or workflow change reaches the page's
 * live feed.
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
  /** The step whose panel is open: its words are highlighted. */
  selectedStep?: string | null;
  /** The rule whose editor is open: its sentence is highlighted. */
  selectedRule?: string | null;
  /** A step's text was chosen: the page opens its panel (and returns focus to `trigger`). */
  onOpenStep?: (id: string, trigger: HTMLElement) => void;
  /** A rule's sentence was chosen: the page opens its editor. */
  onOpenRule?: (id: string, trigger: HTMLElement) => void;
}

const errorMessage = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** The same hint the Detailed editor gives (d11). */
const HINT = "Start with a verb, one line: 'Packs the commits for the hand-off.'";

type Target = { kind: "workflow" } | null;

/** The words' look when not editing: the API's stand-in, the visible gap, or plain. */
function textTone(standin: boolean, value: string): string {
  if (standin) return "sv-standin";
  return value ? "" : "sv-gap";
}

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
      // A span, not a <button>: a button is an atomic inline box and would not wrap with the text
      // (S6819 accepted: a <button>, even reset to `display: inline`, lays out as one unbreakable box).
      <span // NOSONAR
        role="button"
        tabIndex={busy ? -1 : 0}
        aria-disabled={busy || undefined}
        className={`sv-text ${textTone(standin, value)} ${className ?? ""}`.trim()}
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

type Opening = Readonly<{
  selectedStep?: string | null;
  selectedRule?: string | null;
  onOpenStep?: (id: string, trigger: HTMLElement) => void;
  onOpenRule?: (id: string, trigger: HTMLElement) => void;
}>;

/** The small "disabled" tag after a disabled rule's or step's words (d13, amends d8). */
const Pill = () => (
  <span className="sv-pill" aria-hidden="true">
    disabled
  </span>
);

/**
 * Words that open something when chosen: a span with a button role, so it wraps with the text
 * (S6819 accepted: a <button> lays out as one unbreakable inline box, whatever its `display`).
 */
function Opener({
  label,
  className,
  selected,
  onOpen,
  children,
}: Readonly<{
  label: string;
  className: string;
  selected: boolean;
  onOpen?: (trigger: HTMLElement) => void;
  children: React.ReactNode;
}>) {
  return (
    <span // NOSONAR
      role="button"
      tabIndex={0}
      className={`${className}${selected ? " sv-selected" : ""}`}
      aria-pressed={selected}
      title={label}
      data-open-label={label}
      onClick={(e) => onOpen?.(e.currentTarget)}
      onKeyDown={(e) => {
        if (e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        onOpen?.(e.currentTarget);
      }}
    >
      {children}
    </span>
  );
}

/** Splits a closing period off `text`: `[words, "."]`, or `[text, ""]` when it has none. */
function splitPeriod(text: string): [string, string] {
  return text.endsWith(".") ? [text.slice(0, -1), "."] : [text, ""];
}

/** A disabled segment's closing period comes off, so the tag sits before it (d15). */
function beforePeriod(seg: ParagraphSegment, text: string): [string, string] {
  return seg.disabled === true ? splitPeriod(text) : [text, ""];
}

/** A step's lead-in: a join inside a sentence ("; then", " and", ",") reads as plain text (d15). */
function Lead({ seg, phrase }: Readonly<{ seg: ParagraphSegment; phrase: string }>) {
  if (!phrase) return null;
  if (seg.grouped === true) return <>{phrase}</>;
  return <span className="sv-phrase">{phrase}</span>;
}

/** One step's words in its sentence: `phrase shown tail`, nothing composed here (o4). */
function StepPart({ seg, selectedStep, onOpenStep }: Readonly<{ seg: ParagraphSegment } & Opening>) {
  const id = str(seg.step);
  const phrase = str(seg.phrase);
  const shown = str(seg.shown) || str(seg.text);
  const [tail, period] = beforePeriod(seg, str(seg.tail));
  const name = str(seg.name) || id;
  const standin = seg.standin === true;
  const selected = selectedStep === id;
  return (
    <span className={seg.disabled ? "sv-step sv-disabled" : "sv-step"} data-step={id}>
      {seg.disabled ? <span className="sv-sr">Disabled step: </span> : null}
      <Lead seg={seg} phrase={phrase} />
      {phrase ? " " : ""}
      {seg.fallback === true ? (
        <span className="sv-fallback">{shown}</span>
      ) : (
        <Opener
          label={`Open the step ${name}`}
          className={standin ? "sv-text sv-standin" : "sv-text"}
          selected={selected}
          onOpen={(el) => onOpenStep?.(id, el)}
        >
          {shown}
          {standin ? <span className="sv-sr"> (not written yet)</span> : null}
        </Opener>
      )}
      {tail}
      {seg.disabled ? <Pill /> : null}
      {period}
    </span>
  );
}

/** A sentence that starts the workflow: it carries its rule's id and opens the rule's editor. */
function RulePart({ seg, selectedRule, onOpenRule }: Readonly<{ seg: ParagraphSegment } & Opening>) {
  const rule = str(seg.rule);
  const [words, period] = beforePeriod(seg, str(seg.text));
  const text = (
    <>
      {seg.disabled ? <span className="sv-sr">Disabled: </span> : null}
      {words}
    </>
  );
  return (
    <span className={seg.disabled ? "sv-text-part sv-disabled" : "sv-text-part"} data-rule={rule || undefined}>
      {rule ? (
        <Opener
          label={`Open the rule ${rule}`}
          className="sv-text"
          selected={selectedRule === rule}
          onOpen={(el) => onOpenRule?.(rule, el)}
        >
          {text}
        </Opener>
      ) : (
        text
      )}
      {seg.disabled ? <Pill /> : null}
      {period}
    </span>
  );
}

/**
 * A segment's key within its sentence, from its own data: a step's id, or the part, rule and
 * sentence of the one segment a start or Then sentence has.
 */
function segmentKey(seg: ParagraphSegment): string {
  if (seg.part === "step") return `step:${str(seg.step)}`;
  return `${seg.part}:${str(seg.rule)}:${seg.sentence ?? ""}`;
}

type Line = { key: string; segs: ParagraphSegment[] };

/** The paragraph, one line per sentence (d11): the API's segments, grouped by `sentence`. */
function Lines({ segments, ...rest }: Readonly<{ segments: ParagraphSegment[] } & Opening>) {
  const lines: Line[] = [];
  let line: Line | undefined;
  segments.forEach((seg, i) => {
    const key = seg.sentence === undefined ? `own-${i}` : `sentence-${seg.sentence}`;
    if (line?.key !== key) {
      line = { key, segs: [] };
      lines.push(line);
    }
    line.segs.push(seg);
  });
  return (
    <>
      {lines.map(({ key, segs }, i) => {
        const depth = Number(segs[0].depth ?? 0);
        // A little space where the paragraph moves from what starts it to its steps, and
        // from its steps to what happens afterwards (d15).
        const part = String(segs[0].part ?? "step");
        const band = i > 0 && part !== String(lines[i - 1].segs[0].part ?? "step") ? " sv-line--band" : "";
        return (
          <p key={key} className={`${depth > 0 ? "sv-line sv-line--in" : "sv-line"}${band}`}>
            {segs.map((seg) =>
              seg.part === "step" ? (
                <StepPart key={segmentKey(seg)} seg={seg} {...rest} />
              ) : (
                <RulePart key={segmentKey(seg)} seg={seg} {...rest} />
              ),
            )}
            {"\n"}
          </p>
        );
      })}
    </>
  );
}

export function SimpleView({ def, live, onSaved, ...opening }: Readonly<SimpleViewProps>) {
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
          <Lines segments={segments} {...opening} />
        </div>
      ) : null}
      {paragraph || problem ? null : (
        // <output> is the status (polite live region); the <p> keeps the paragraph's spacing.
        <p className="sv-loading">
          <output>Reading how it runs…</output>
        </p>
      )}
    </section>
  );
}

export default SimpleView;
