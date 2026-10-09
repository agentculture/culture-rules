import { useEffect, useRef } from "react";

/**
 * Return keyboard focus to the control that opened something (a form, a panel) once it closes
 * — by save, cancel or Escape — so the reader is not dropped at the top of the page.
 */
export function useFocusReturn<T extends HTMLElement>(open: boolean) {
  const ref = useRef<T>(null);
  const was = useRef(open);
  useEffect(() => {
    if (was.current && !open) ref.current?.focus();
    was.current = open;
  }, [open]);
  return ref;
}
