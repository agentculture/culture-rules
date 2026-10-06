import { useEffect } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import AgentStateScript from "./agent-state/AgentStateScript";
import { setAgentState } from "./agent-state/store";
import Header from "./components/Header";
import Rules from "./routes/Rules";
import Workflows from "./routes/Workflows";
import Actors from "./routes/Actors";
import Variables from "./routes/Variables";
import Statistics from "./routes/Statistics";

/**
 * The shell: header + exactly five tab routes. Every other path — including
 * /runs and /history, which are never top-level (issue #2) — lands on Rules.
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
        <Route path="/rules/:ruleId?" element={<Rules />} />
        <Route path="/workflows" element={<Workflows />} />
        <Route path="/actors" element={<Actors />} />
        <Route path="/variables" element={<Variables />} />
        <Route path="/statistics" element={<Statistics />} />
        <Route path="*" element={<Navigate to="/rules" replace />} />
      </Routes>
      <AgentStateScript />
    </div>
  );
}

export default App;
