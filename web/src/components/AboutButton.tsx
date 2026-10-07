import { useCallback, useEffect, useId, useRef, useState } from "react";
import { getDescription, type Description } from "../api/describe";
import { ApiError } from "../api/client";
import "./about.css";

type Loaded = { doc: Description | null; error: string | null };

/**
 * The (i) button (d19): "About <name>". It opens a non-modal panel anchored to
 * itself with the definition's plain description — the same lines as
 * `culture-rules rules describe` / `workflows describe`, fetched from the API
 * (`GET /<noun>/{id}/describe`) each time it opens, shown monospace so the
 * indentation and the symbols (∈ ∉ ≠ ×) read exactly as the CLI prints them.
 *
 * Keyboard: Enter / Space on the button opens it and moves focus into the
 * panel; Escape (anywhere inside) closes it and returns focus to the button.
 * A pointer press outside closes it too. Clicks never reach the row it sits in.
 */
export function AboutButton({
  noun,
  id,
  name,
  stale = false,
}: Readonly<{
  noun: "rules" | "workflows";
  id: string;
  name: string;
  /** The open draft has unsaved edits: the description is of the saved version. */
  stale?: boolean;
}>) {
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [copied, setCopied] = useState(false);
  const panelId = useId();
  const wrap = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  const close = useCallback(() => {
    setOpen(false);
    button.current?.focus();
  }, []);

  // Escape while focus is in the button or the panel, only while open (a closed button
  // leaves Escape to whatever is around it). On `document`, after React's handlers.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (!(e.target instanceof Node) || !wrap.current?.contains(e.target)) return;
      e.preventDefault();
      e.stopPropagation();
      close();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, close]);

  useEffect(() => {
    if (!open) return;
    setLoaded(null);
    setCopied(false);
    const controller = new AbortController();
    getDescription(noun, id, controller.signal)
      .then((doc) => setLoaded({ doc, error: null }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setLoaded({ doc: null, error: err instanceof ApiError ? err.message : String(err) });
      });
    panel.current?.focus();
    return () => controller.abort();
  }, [open, noun, id]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (e.target instanceof Node && !wrap.current?.contains(e.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  const text = loaded?.doc?.lines.join("\n") ?? "";
  const copy = () => {
    void navigator.clipboard?.writeText(text).then(
      () => setCopied(true),
      () => setCopied(false),
    );
  };

  return (
    <span className="about" ref={wrap} onClick={(e) => e.stopPropagation()}>
      <button
        ref={button}
        type="button"
        className="about__button"
        aria-label={`About ${name}`}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <circle cx="12" cy="12" r="9.5" />
          <path d="M12 11v6M12 7.5v.01" strokeWidth="2.4" />
        </svg>
      </button>
      <div
        id={panelId}
        ref={panel}
        className="about__panel"
        role="dialog"
        aria-modal="false"
        aria-label={`About ${name}`}
        tabIndex={-1}
        hidden={!open}
        draggable={false}
        onDragStart={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
      >
        {open && loaded === null ? <p className="about__note">Reading…</p> : null}
        {open && loaded?.error ? <p className="about__note about__note--error">{loaded.error}</p> : null}
        {open && loaded?.doc ? (
          <>
            {stale ? <p className="about__note">The saved version.</p> : null}
            <pre className="about__lines" data-testid="about-lines">
              {text}
            </pre>
            <div className="about__actions">
              <button type="button" className="about__action" onClick={copy}>
                Copy
              </button>
              <span className="about__copied" aria-live="polite">
                {copied ? "Copied" : ""}
              </span>
              <button type="button" className="about__action" onClick={close}>
                Close
              </button>
            </div>
          </>
        ) : null}
      </div>
    </span>
  );
}

export default AboutButton;
