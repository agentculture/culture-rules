import { useEffect, useRef, type ReactNode } from "react";
import { useEscapeKey } from "../hooks/useEscapeKey";

/**
 * A non-modal floating panel, a native `<dialog open>` (not `showModal()`, so
 * the canvas stays live behind it): focus moves into it on open, Escape
 * closes it, and focus returns to whatever opened it.
 */
export function Panel({
  label,
  onClose,
  returnFocus,
  className = "",
  children,
}: Readonly<{
  label: string;
  onClose: () => void;
  returnFocus?: HTMLElement | null;
  className?: string;
  children: ReactNode;
}>) {
  const ref = useRef<HTMLDialogElement>(null);
  useEscapeKey(ref, onClose);
  useEffect(() => {
    const first = ref.current?.querySelector<HTMLElement>(
      "input:not([type=hidden]):not([tabindex='-1']), select, textarea, button",
    );
    first?.focus();
    return () => {
      if (returnFocus && document.contains(returnFocus)) returnFocus.focus();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <dialog ref={ref} open aria-label={label} className={`wf-panel ${className}`}>
      {children}
    </dialog>
  );
}

export default Panel;
