import { useRef } from "react";

/**
 * The value as it was when `open` became true, held until it is false again: what an edit form
 * was opened on (c27). A live refresh may bring newer rules meanwhile; the form still compares,
 * and the fold writes still re-read, against the snapshot the author saw, so a concurrent edit
 * is skipped and flagged rather than overwritten with the form's older inputs.
 */
export function useFrozen<T>(open: boolean, value: T): T {
  const held = useRef<{ open: boolean; value: T }>({ open, value });
  if (!open || !held.current.open) held.current = { open, value };
  return held.current.value;
}
