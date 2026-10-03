import { useTabReady } from "./useTabReady";

/**
 * The Statistics tab — shell only. Its 'Chosen — Statistics' board on the design canvas
 * is implemented by a later task; this page holds the route, the heading
 * and the agent-state contract so the shell is walkable end to end.
 */
export function Statistics() {
  useTabReady("statistics", true);
  return (
    <main id="main" className="page page--placeholder" tabIndex={-1}>
      <h1 className="page__title">Statistics</h1>
      <p className="page__lede">What every machine is doing, and how it went.</p>
    </main>
  );
}

export default Statistics;
