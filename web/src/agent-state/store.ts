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
export type Tab = "rules" | "workflows" | "actors" | "variables" | "statistics";

export interface AgentIdentity {
  status: "loading" | "signed-in" | "unauthenticated" | "unavailable";
  /** WhoAmI.identity */
  identity: string | null;
  /** WhoAmI.kind: sso | service | agent */
  kind: string | null;
  /** The highest of WhoAmI.roles (viewer < editor < admin). */
  role: string | null;
}

export interface AgentRulesState {
  count: number;
  selected: string | null;
  /** The stages drawn for the selected rule, in order. */
  stages: string[];
}

/** The Workflows tab's slice (src/workflows/agentState.ts writes it). */
export interface AgentWorkflowsState {
  /** Workflows listed. */
  count: number;
  /** The workflow on the canvas (?id=). */
  selected: string | null;
  /** Its step ids, in definition order (unsaved edits included). */
  steps: string[];
  /** The selected step, if any. */
  step: string | null;
  /** Unsaved edits exist. */
  dirty: boolean;
  /** The run overlaid on the canvas (?run=), from persisted run state. */
  run: { id: string; status: string } | null;
}

/** The Actors tab's slice. */
export interface AgentActorsState {
  /** Actors listed (all kinds). */
  count: number;
  /** Actors shown under the current filter. */
  shown: number;
  /** The kind filter ("all" for every kind). */
  kind: string;
  selected: string | null;
}

/** The Statistics tab's slice. */
export interface AgentStatisticsState {
  /** One lane per enrolled machine, in lane order. */
  machines: string[];
  offline: string[];
  range: string;
  view: "lanes" | "table";
  /** Where load/queue came from: the status endpoint, or derived from runs. */
  source: "machines/status" | "runs";
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
  workflows?: AgentWorkflowsState | null;
  actors?: AgentActorsState | null;
  statistics?: AgentStatisticsState | null;
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
  return JSON.stringify(state, null, 2).replaceAll("<", String.raw`\u003c`);
}
