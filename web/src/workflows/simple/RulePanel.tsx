/**
 * The right pane Simple opens for a rule's sentence (d14): Detailed's own entry-point editor
 * (views/EntryEditors.tsx) with that rule open, so its enable / disable switch, the "Stop N
 * current runs?" offer and every other field behave as they do in Detailed. Escape or Close
 * puts it away and focus returns to the sentence.
 */
import type { WorkflowDef } from "../../api/workflows";
import Panel from "../Panel";
import EntryEditors from "../views/EntryEditors";
import type { LiveFeed } from "./liveFeed";

export function RulePanel({
  workflow,
  ruleId,
  live,
  hrefFor,
  returnFocus,
  onClose,
}: Readonly<{
  workflow: WorkflowDef;
  ruleId: string;
  live?: LiveFeed;
  hrefFor?: (workflowId: string) => string;
  returnFocus?: HTMLElement | null;
  onClose: () => void;
}>) {
  return (
    <Panel label="Rule editor" className="wf-panel--wide wf-panel--rule" returnFocus={returnFocus} onClose={onClose}>
      <button type="button" className="wf-button wf-panel__close" onClick={onClose}>
        Close
      </button>
      <EntryEditors workflow={workflow} entry={ruleId} live={live} hrefFor={hrefFor} />
    </Panel>
  );
}

export default RulePanel;
