import { getAgentState, setAgentState, type AgentState } from "../agent-state/store";

/**
 * The Workflows tab's slice of `#agent-state`. The shared store's type is
 * owned by the shell, so the slice is added here, additively, under the key
 * `workflows`:
 *
 *   { count, selected, steps, step, dirty, run: { id, status } | null }
 */
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

type WithWorkflows = AgentState & { workflows?: AgentWorkflowsState | null };

export function setWorkflowsState(state: AgentWorkflowsState | null): void {
  setAgentState({ workflows: state } as unknown as Partial<Omit<AgentState, "status">>);
}

export function workflowsState(): AgentWorkflowsState | null {
  return (getAgentState() as WithWorkflows).workflows ?? null;
}
