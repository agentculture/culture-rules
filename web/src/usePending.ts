import { useCallback, useRef, useState } from "react";

/**
 * Per-id in-flight tracking for toggles. `run(id, work)` ignores re-entry while
 * `id` is pending (the ref answers synchronously, before state re-renders);
 * `pending` is the render-side set a Switch reads to show its disabled state.
 */
export function usePending() {
  const inflight = useRef(new Set<string>());
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());

  const run = useCallback(async (id: string, work: () => Promise<void>): Promise<void> => {
    if (inflight.current.has(id)) return;
    inflight.current.add(id);
    setPending(new Set(inflight.current));
    try {
      await work();
    } finally {
      inflight.current.delete(id);
      setPending(new Set(inflight.current));
    }
  }, []);

  return { pending, run };
}
