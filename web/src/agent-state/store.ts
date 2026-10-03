/**
 * The agent-state store — the machine-readable mirror of what this page is
 * showing (mirrors culture-nodes' web/src/agent-state/store.ts).
 *
 * Serialized into one `<script type="application/json" id="agent-state">`
 * node an agent (webglass, Playwright) reads with a single selector instead
 * of scraping the DOM. `status: "ready"` means the current view finished its
 * initial load — including finishing it badly: a load error is listed in
 * `errors` and rendered alongside, it does not keep the page "loading".
 */

export type AgentStatus = "loading" | "ready";
export type Tab = "rules" | "workflows" | "actors" | "statistics";

export interface AgentIdentity {
  status: "loading" | "signed-in" | "unauthenticated" | "unavailable";
  subject: string | null;
  role: string | null;
  /** True when the API has no /whoami yet and the identity is the stand-in. */
  mocked: boolean;
}

export interface AgentRulesState {
  count: number;
  selected: string | null;
  /** The stages drawn for the selected rule, in order. */
  stages: string[];
}

export interface AgentState {
  /**
   * Derived, never set directly: `ready` once the current view has finished
   * its initial load (`view_ready`) AND identity is no longer loading.
   */
  status: AgentStatus;
  /** The current view finished its initial load (well or badly). */
  view_ready: boolean;
  route: string;
  tab: Tab | null;
  identity: AgentIdentity | null;
  /** Load errors the current view is showing; empty when all is well. */
  errors: string[];
  rules?: AgentRulesState | null;
}

const INITIAL: AgentState = {
  status: "loading",
  view_ready: false,
  route: "/",
  tab: null,
  identity: null,
  errors: [],
};

let current: AgentState = INITIAL;
const listeners = new Set<() => void>();

export function getAgentState(): AgentState {
  return current;
}

export function subscribeAgentState(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Merge a patch. No-op when nothing actually changed (compared by value), so
 * a re-render storm cannot make the `<script>` node churn.
 */
export function setAgentState(patch: Partial<Omit<AgentState, "status">>): void {
  const merged: AgentState = { ...current, ...patch };
  const identityLoading = merged.identity?.status === "loading";
  const next: AgentState = {
    ...merged,
    status: merged.view_ready && !identityLoading ? "ready" : "loading",
  };
  if (JSON.stringify(next) === JSON.stringify(current)) return;
  current = next;
  for (const listener of listeners) listener();
}

/** Test seam: drop back to the initial state. */
export function resetAgentState(): void {
  current = INITIAL;
  for (const listener of listeners) listener();
}

/**
 * Serialize for embedding inside a `<script>` element. `<` is escaped so a
 * value containing `</script>` cannot close the element early.
 */
export function serializeAgentState(state: AgentState): string {
  return JSON.stringify(state, null, 2).replace(/</g, "\\u003c");
}
