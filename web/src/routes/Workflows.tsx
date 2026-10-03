import { useTabReady } from "./useTabReady";

/**
 * The Workflows tab — shell only. Its 'Chosen — Workflows' board on the design canvas
 * is implemented by a later task; this page holds the route, the heading
 * and the agent-state contract so the shell is walkable end to end.
 */
export function Workflows() {
  useTabReady("workflows", true);
  return (
    <main id="main" className="page page--placeholder" tabIndex={-1}>
      <h1 className="page__title">Workflows</h1>
      <p className="page__lede">How reusable work is done: steps, branches, waits.</p>
    </main>
  );
}

export default Workflows;
