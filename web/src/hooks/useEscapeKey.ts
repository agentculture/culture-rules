import { useEffect, useRef, type RefObject } from "react";

/**
 * Call `onEscape` when Escape is pressed while focus is inside `ref`'s element
 * — a form or a floating panel closing itself — without putting a keyboard
 * listener on that non-interactive container.
 *
 * The listener sits on `document`, so it runs after every React handler: an
 * inner widget that handles Escape itself (and says so with preventDefault)
 * wins, and the innermost container handles an Escape only once — it claims
 * the key and stops it from reaching listeners further out.
 */
export function useEscapeKey(ref: RefObject<HTMLElement | null>, onEscape: () => void): void {
  const latest = useRef(onEscape);
  useEffect(() => {
    latest.current = onEscape;
  }, [onEscape]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const el = ref.current;
      if (!el || !(event.target instanceof Node) || !el.contains(event.target)) return;
      event.preventDefault();
      event.stopPropagation();
      latest.current();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [ref]);
}
