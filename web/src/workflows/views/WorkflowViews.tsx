/**
 * A workflow's canvas in one of three views (spec c11, h6):
 *
 *   Simple   — the When / Then workflow (canvas Fold-Editor). Its component
 *              lands with t6; until then the caller may pass it as `simple`,
 *              and a placeholder stands in.
 *   Detailed — the steps-and-edges canvas (canvas WF-Flow), compact: cards
 *              without port rows, one edge per connected pair labelled with
 *              its wire count; the selected card expands to its ports and
 *              its wires (../Canvas.tsx, with the same props).
 *   Debug    — every port, its type and reference, with upstream/downstream
 *              highlighting on port selection (canvas WF-Variables,
 *              WF-Variables-Port): ./DebugView.tsx, its own layout.
 *
 * The chosen view is kept per viewer in localStorage (./mode.ts).
 */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import WorkflowCanvas, { type CanvasProps } from "../Canvas";
import DebugView from "./DebugView";
import { readViewMode, writeViewMode, type ViewMode } from "./mode";
import ViewSwitch from "./ViewSwitch";
import "./views.css";

export interface WorkflowViewsProps extends CanvasProps {
  /** The Simple view (t6); a placeholder renders when it is not given. */
  simple?: ReactNode;
  /** Extra toolbar content, after the switch (status chips, actions). */
  toolbar?: ReactNode;
  /** Told the view on mount and on every switch (agent-state reports it). */
  onModeChange?: (mode: ViewMode) => void;
}

function SimplePlaceholder() {
  return (
    <section className="wf-view-placeholder" aria-label="Simple view">
      <p className="wf-view-placeholder__lead">The When / Then view is on its way.</p>
      <p className="wf-view-placeholder__hint">Detailed and Debug show this workflow now.</p>
    </section>
  );
}

export function WorkflowViews({ simple, toolbar, onModeChange, ...canvas }: Readonly<WorkflowViewsProps>) {
  const [mode, setMode] = useState<ViewMode>(readViewMode);
  useEffect(() => onModeChange?.(mode), [mode, onModeChange]);
  const choose = useCallback((next: ViewMode) => {
    setMode(next);
    writeViewMode(next);
  }, []);

  let view: ReactNode;
  if (mode === "detailed") view = <WorkflowCanvas {...canvas} />;
  else if (mode === "debug") view = <DebugView workflow={canvas.workflow} />;
  else view = simple ?? <SimplePlaceholder />;

  return (
    <div className="wf-views" data-view={mode}>
      <div className="wf-views__bar">
        <ViewSwitch mode={mode} onChange={choose} />
        {toolbar}
      </div>
      {view}
    </div>
  );
}

export default WorkflowViews;
