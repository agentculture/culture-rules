import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { getDescription, type Description } from "../api/describe";
import { ApiError } from "../api/client";
import "./about.css";

/** A finished describe call, keyed by the noun/id it was for. */
type Loaded = { key: string; doc: Description | null; error: string | null };

/** The phone-width layout, where about.css docks the panel to the screen's bottom. */
const DOCKED = "(max-width: 640px)";

/**
 * The (i) button (d19): "About <name>". It opens a non-modal panel anchored to
 * itself with the definition's plain description — the same lines as
 * `culture-rules rules describe` / `workflows describe`, fetched from the API
 * (`GET /<noun>/{id}/describe`) each time it opens, shown monospace so the
 * indentation and the symbols (∈ ≠ × ≤) read exactly as the CLI prints them.
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
  const [stored, setLoaded] = useState<Loaded | null>(null);
  // Only this noun/id's answer is ever shown: a reused button (a new id) starts empty.
  const key = `${noun}/${id}`;
  const loaded = stored?.key === key ? stored : null;
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
    // Every open re-reads the description, but the last answer for this id stays on screen
    // until the fresh one lands. Clearing it first would swap the panel's content for
    // "Reading…" and back, remounting Copy and Close under a reader (or a test) mid-click.
    setCopied(false);
    const controller = new AbortController();
    const forKey = `${noun}/${id}`;
    getDescription(noun, id, controller.signal)
      .then((doc) => setLoaded({ key: forKey, doc, error: null }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setLoaded({ key: forKey, doc: null, error: err instanceof ApiError ? err.message : String(err) });
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

  // Keep the open panel inside the window: as wide as its lines need, never wider than the
  // window less a 16px gutter each side, and moved left of its button when it would pass the
  // right edge. At phone width the CSS docks it to the screen's bottom instead.
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const el = panel.current;
      const anchor = wrap.current;
      if (!el || !anchor) return;
      el.style.left = "";
      el.style.maxWidth = "";
      if (window.matchMedia?.(DOCKED)?.matches) return;
      const gutter = 16;
      const room = window.innerWidth - 2 * gutter;
      el.style.left = "0px";
      el.style.maxWidth = `min(44rem, ${room}px)`;
      const from = anchor.getBoundingClientRect().left;
      const width = el.getBoundingClientRect().width;
      const left = Math.max(gutter, Math.min(from, window.innerWidth - gutter - width));
      el.style.left = `${left - from}px`;
    };
    place();
    // Re-place when the window or the panel itself changes size (its mono face may load late).
    window.addEventListener("resize", place);
    let width = panel.current?.getBoundingClientRect().width ?? 0;
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => {
            const now = panel.current?.getBoundingClientRect().width ?? 0;
            if (Math.abs(now - width) < 0.5) return; // our own move: no loop
            width = now;
            place();
          });
    if (panel.current) observer?.observe(panel.current);
    return () => {
      window.removeEventListener("resize", place);
      observer?.disconnect();
    };
  }, [open, loaded]);

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
