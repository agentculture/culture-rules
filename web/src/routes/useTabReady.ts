import { useEffect } from "react";
import { setAgentState, type TabId } from "../agent-state/store";

/** Report the current tab to #agent-state; `ready` once its first load settled. */
export function useTabReady(tab: TabId, ready: boolean, errors: string[] = []) {
  const key = errors.join("\n");
  useEffect(() => {
    setAgentState({ tab, view_ready: ready, errors: key ? key.split("\n") : [] });
  }, [tab, ready, key]);
}
