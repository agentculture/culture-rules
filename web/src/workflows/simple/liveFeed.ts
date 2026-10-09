import { useCallback, useEffect, useMemo, useRef } from "react";
import type { LiveChange } from "../../api/live";

/**
 * A live-change feed handed down from the page that owns the one EventSource
 * (the Workflows tab), so the Simple view inside it opens no second stream:
 * the owner `emit`s each batch, the Simple view `subscribe`s.
 */
export interface LiveFeed {
  subscribe: (listener: (changes: LiveChange[]) => void) => () => void;
  emit: (changes: LiveChange[]) => void;
}

/** A stable feed for the owner to emit into and pass down. */
export function useLiveFeed(): LiveFeed {
  const listeners = useRef(new Set<(changes: LiveChange[]) => void>());
  const subscribe = useCallback((listener: (changes: LiveChange[]) => void) => {
    listeners.current.add(listener);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);
  const emit = useCallback((changes: LiveChange[]) => {
    for (const listener of listeners.current) listener(changes);
  }, []);
  return useMemo(() => ({ subscribe, emit }), [subscribe, emit]);
}

/** Hand each batch on `feed` (when there is one) to `onChange`. */
export function useFeedSubscription(feed: LiveFeed | undefined, onChange: (changes: LiveChange[]) => void) {
  const handler = useRef(onChange);
  handler.current = onChange;
  useEffect(() => feed?.subscribe((changes) => handler.current(changes)), [feed]);
}
