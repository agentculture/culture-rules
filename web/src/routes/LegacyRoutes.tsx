import { useEffect, useState } from "react";
import { Navigate, useLocation, useParams } from "react-router-dom";
import { listRules } from "../api/client";
import { ruleRedirect, rulesUnavailableRedirect, workflowPath, type Redirect } from "./legacy-redirects";
import { useTabReady } from "./useTabReady";

/**
 * `/rules` and `/rules/:ruleId`: the Rules tab folded into Workflows (t8).
 * Reads the rules once to find where the rule now lives, then replaces the
 * history entry (an old bookmark never leaves a dead page behind). A failed
 * read still lands on /workflows, saying the rule could not be looked up.
 */
export function RuleRedirect() {
  const { ruleId } = useParams();
  const [target, setTarget] = useState<Redirect | null>(ruleId ? null : ruleRedirect(undefined, []));
  // Where it lands is the Workflows tab: say so while looking (never ready: the tab reports that).
  useTabReady("workflows", false);
  useEffect(() => {
    if (!ruleId) return;
    const controller = new AbortController();
    listRules(controller.signal).then(
      (rules) => {
        if (!controller.signal.aborted) setTarget(ruleRedirect(ruleId, rules));
      },
      () => {
        if (!controller.signal.aborted) setTarget(rulesUnavailableRedirect(ruleId));
      },
    );
    return () => controller.abort();
  }, [ruleId]);
  if (!target) {
    return (
      <main id="main" className="redirect-note" tabIndex={-1}>
        <output>Finding where this rule lives now…</output>
      </main>
    );
  }
  return <Navigate to={target.to} replace />;
}

/** `/workflows/:workflowId` is the same page as `/workflows?id=…`. */
export function WorkflowPathRedirect() {
  const { workflowId = "" } = useParams();
  const { search } = useLocation();
  return <Navigate to={workflowPath(workflowId, search)} replace />;
}
