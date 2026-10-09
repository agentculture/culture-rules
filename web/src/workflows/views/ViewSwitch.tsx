/**
 * The workflow toolbar's Simple / Detailed / Debug switch (canvas Fold-Editor,
 * WF-Flow, WF-Variables): a segmented group of toggle buttons, the pressed one
 * filled ink. A plain button group, so Tab reaches each mode and Enter / Space
 * chooses it.
 */
import { VIEW_MODES, VIEW_MODE_LABELS, type ViewMode } from "./mode";

export interface ViewSwitchProps {
  mode: ViewMode;
  onChange: (mode: ViewMode) => void;
}

export function ViewSwitch({ mode, onChange }: Readonly<ViewSwitchProps>) {
  return (
    <fieldset aria-label="Canvas view" className="wf-view-switch plain-group">
      {VIEW_MODES.map((m) => (
        <button
          key={m}
          type="button"
          className="wf-view-switch__button"
          aria-pressed={m === mode}
          onClick={() => onChange(m)}
        >
          {VIEW_MODE_LABELS[m]}
        </button>
      ))}
    </fieldset>
  );
}

export default ViewSwitch;
