import { useEffect } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import AgentStateScript from "./agent-state/AgentStateScript";
import { setAgentState } from "./agent-state/store";
import Header from "./components/Header";
import Workflows from "./routes/Workflows";
import Actors from "./routes/Actors";
import Variables from "./routes/Variables";
import Statistics from "./routes/Statistics";
import { RuleRedirect, WorkflowPathRedirect } from "./routes/LegacyRoutes";

/**
 * The shell: header + exactly four tab routes (rules are folded into
 * Workflows, spec c16). Old /rules links redirect to where the rule lives now
 * (routes/legacy-redirects.ts); every other path — including /runs and
 * /history, which are never top-level (issue #2) — lands on Workflows.
 */
export function App() {
  const location = useLocation();
  useEffect(() => {
    setAgentState({ route: location.pathname });
  }, [location.pathname]);

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Header />
      <Routes>
        <Route path="/rules/:ruleId?" element={<RuleRedirect />} />
        <Route path="/workflows" element={<Workflows />} />
        <Route path="/workflows/:workflowId" element={<WorkflowPathRedirect />} />
        <Route path="/actors" element={<Actors />} />
        <Route path="/variables" element={<Variables />} />
        <Route path="/statistics" element={<Statistics />} />
        <Route path="*" element={<Navigate to="/workflows" replace />} />
      </Routes>
      <AgentStateScript />
    </div>
  );
}

export default App;
