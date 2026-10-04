import type { RefObject } from "react";
import { Link } from "react-router-dom";
import type { WorkflowDef } from "../api/workflows";
import { MachineDot, Switch, machineStyle } from "../culture-design/stages";

interface Props {
  workflows: readonly WorkflowDef[];
  selectedId: string | null;
  /** The machine palette slot a workflow's dot (and selection ring) wears; null = neutral. */
  slotOf: (wf: WorkflowDef) => number | null;
  onToggle: (wf: WorkflowDef) => void;
  /** Workflow ids whose toggle request is in flight (their switch is disabled). */
  pending?: ReadonlySet<string>;
  onNew: () => void;
  /** A row's link was followed (the board closes what was open for the old selection). */
  onOpen?: (id: string) => void;
  /** The New button, so a closed name form can return focus to its opener. */
  newRef?: RefObject<HTMLButtonElement>;
}

const rowClass = (selected: boolean, enabled: boolean) =>
  `rule-row wf-row${selected ? " is-selected" : ""}${enabled ? "" : " is-disabled"}`;

/**
 * The left column of the Workflows tab, aligned with the Rules board's list
 * (RuleList): the dashed "New workflow" affordance, then one row per workflow
 * — machine dot, name (a link to `/workflows?id=<id>`), enable switch. It is
 * always there, with no workflows or one.
 */
export function WorkflowList({ workflows, selectedId, slotOf, onToggle, pending, onNew, onOpen, newRef }: Readonly<Props>) {
  return (
    <nav className="rule-list wf-list" aria-label="Workflows">
      <button ref={newRef} type="button" className="rule-list__new" onClick={onNew}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
          <path d="M12 5v14M5 12h14" />
        </svg>
        New workflow
      </button>
      {workflows.map((wf) => {
        const enabled = wf.enabled !== false;
        const isSelected = wf.id === selectedId;
        const slot = slotOf(wf);
        return (
          <div key={wf.id} className={rowClass(isSelected, enabled)} data-workflow-id={wf.id} style={machineStyle(slot)}>
            <MachineDot slot={slot} />
            <Link
              className="rule-row__name"
              to={`/workflows?id=${encodeURIComponent(wf.id)}`}
              aria-current={isSelected ? "true" : undefined}
              onClick={() => onOpen?.(wf.id)}
            >
              {wf.name}
            </Link>
            <Switch label={`${wf.name} enabled`} checked={enabled} disabled={pending?.has(wf.id)} onChange={() => onToggle(wf)} />
          </div>
        );
      })}
    </nav>
  );
}
