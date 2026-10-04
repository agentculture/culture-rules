import { useId, type ReactNode } from "react";

const XIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
    <path d="M6 6l12 12M18 6L6 18" />
  </svg>
);

/** A round remove button, a 44px target named for what it removes. */
export function RemoveButton({ label, onClick }: Readonly<{ label: string; onClick: () => void }>) {
  return (
    <button type="button" className="icon-button icon-button--danger" aria-label={label} onClick={onClick}>
      <XIcon />
    </button>
  );
}

interface TextFieldProps {
  /** Visible label, also the accessible name unless `ariaLabel` overrides it. */
  label?: string;
  ariaLabel?: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  hint?: string;
  placeholder?: string;
  mono?: boolean;
}

/** A text input whose error and hint are announced with it (aria-describedby) and which flags aria-invalid. */
export function TextField({ label, ariaLabel, value, onChange, error, hint, placeholder, mono }: Readonly<TextFieldProps>) {
  const uid = useId();
  const describedBy = [error ? `${uid}-err` : null, hint ? `${uid}-hint` : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className="actor-form__field">
      {label ? <label htmlFor={`${uid}-in`}>{label}</label> : null}
      <input
        id={`${uid}-in`}
        type="text"
        value={value}
        placeholder={placeholder}
        aria-label={ariaLabel}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={mono ? "mono" : undefined}
        autoComplete="off"
        spellCheck={false}
        onChange={(e) => onChange(e.target.value)}
      />
      {error ? (
        <p id={`${uid}-err`} className="actor-form__error">
          {error}
        </p>
      ) : null}
      {hint ? (
        <p id={`${uid}-hint`} className="actor-form__hint">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function Section({ title, children }: Readonly<{ title: string; children: ReactNode }>) {
  return (
    <fieldset className="actor-subform plain-group" aria-label={title}>
      <h3 className="actor-subform__title" aria-hidden="true">
        {title}
      </h3>
      {children}
    </fieldset>
  );
}
