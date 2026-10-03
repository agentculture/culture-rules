import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useEscapeKey } from "../hooks/useEscapeKey";

interface Props {
  /** The form's accessible name: "New workflow" or "Rename workflow". */
  label: string;
  /** The submit button's text. */
  submitLabel: string;
  /** The name the field starts with ("" for a new workflow). */
  initial?: string;
  /** A call is in flight: the form stays, submit is disabled. */
  busy?: boolean;
  /** The API's answer to the last attempt (validation, conflict, ...), shown inline. */
  error?: string | null;
  onSubmit: (name: string) => void;
  onCancel: () => void;
}

/**
 * The one-question workflow form: only its name (Enter submits, Escape
 * cancels, the field has focus on open). Creation asks it first — steps,
 * ports, wiring and placement then grow on the canvas through the step `+`
 * — and renaming asks it again.
 */
export function WorkflowNameForm({
  label,
  submitLabel,
  initial = "",
  busy = false,
  error = null,
  onSubmit,
  onCancel,
}: Props) {
  const [name, setName] = useState(initial);
  const form = useRef<HTMLFormElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const errorId = useId();
  useEscapeKey(form, onCancel);
  useEffect(() => {
    field.current?.focus();
    field.current?.select();
  }, []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const text = name.trim();
    if (!text || busy) return;
    onSubmit(text);
  };

  return (
    <form ref={form} className="wf-new" aria-label={label} onSubmit={submit}>
      <label className="wf-new__field">
        <span>Name</span>
        <input
          ref={field}
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          autoComplete="off"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
        />
      </label>
      {error ? (
        <p id={errorId} className="wf-new__error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="wf-new__actions">
        <button type="submit" className="wf-button wf-button--primary wf-button--large" disabled={busy}>
          {submitLabel}
        </button>
        <button type="button" className="wf-button wf-button--large" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

export default WorkflowNameForm;
