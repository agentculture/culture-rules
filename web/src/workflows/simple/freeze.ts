import { useRef } from "react";

/**
 * The value as it was when `form` opened, held while that same form stays open: what an edit form
 * was opened on (c27). A live refresh may bring newer rules meanwhile; the form still compares,
 * and the fold writes still re-read, against the snapshot the author saw, so a concurrent edit
 * is skipped and flagged rather than overwritten with the form's older inputs.
 */
export function useFrozen<T>(form: string | null, value: T): T {
  // Keyed by the form that is open: switching straight to another form takes a fresh snapshot.
  const held = useRef<{ form: string | null; value: T }>({ form, value });
  if (form === null || held.current.form !== form) held.current = { form, value };
  return held.current.value;
}
