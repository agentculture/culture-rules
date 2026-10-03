import { getAgentState, setAgentState, type AgentWorkflowsState } from "../agent-state/store";

/**
 * The Workflows tab's slice of `#agent-state`, under the key `workflows`
 * (typed in the shared store as `AgentWorkflowsState`):
 *
 *   { count, selected, steps, step, dirty, run: { id, status } | null }
 */
export type { AgentWorkflowsState };

export function setWorkflowsState(state: AgentWorkflowsState | null): void {
  setAgentState({ workflows: state });
}

export function workflowsState(): AgentWorkflowsState | null {
  return getAgentState().workflows ?? null;
}
