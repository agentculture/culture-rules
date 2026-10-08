import type { ReactNode, RefObject } from "react";
import { Link } from "react-router-dom";
import type { WorkflowDef } from "../api/workflows";
import { MachineDot, Switch, machineStyle } from "../culture-design/stages";
import { AboutButton } from "../components/AboutButton";

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
  /** "Show deleted" is on (the list was loaded with `include_deleted`). */
  showDeleted?: boolean;
  onShowDeleted?: (on: boolean) => void;
  /** Soft-deleted workflows to list (only while `showDeleted`), dimmed, below the live ones. */
  deleted?: readonly WorkflowDef[];
  onRestore?: (wf: WorkflowDef) => void;
  /** Purge is offered to admins only: absent means never rendered. */
  onPurge?: (wf: WorkflowDef) => void;
  /** Ids whose restore is in flight. */
  restoring?: ReadonlySet<string>;
  /** The open purge confirmation panel, below the rows. */
  purgePanel?: ReactNode;
}

const when = (iso?: string | null) => (iso ? iso.slice(0, 10) : "");

const rowClass = (selected: boolean, enabled: boolean) =>
  `rule-row wf-row${selected ? " is-selected" : ""}${enabled ? "" : " is-disabled"}`;

/**
 * The left column of the Workflows tab, aligned with the Rules board's list
 * (RuleList): the dashed "New workflow" affordance, then one row per workflow
 * — machine dot, name (a link to `/workflows?id=<id>`), enable switch. It is
 * always there, with no workflows or one.
 */
export function WorkflowList({
  workflows,
  selectedId,
  slotOf,
  onToggle,
  pending,
  onNew,
  onOpen,
  newRef,
  showDeleted = false,
  onShowDeleted,
  deleted = [],
  onRestore,
  onPurge,
  restoring,
  purgePanel,
}: Readonly<Props>) {
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
            <AboutButton noun="workflows" id={wf.id} name={wf.name} />
            <Switch label={`${wf.name} enabled`} checked={enabled} disabled={pending?.has(wf.id)} onChange={() => onToggle(wf)} />
          </div>
        );
      })}
      {showDeleted &&
        deleted.map((wf) => (
          <div key={wf.id} className="rule-row wf-row is-deleted" data-workflow-id={wf.id}>
            <MachineDot slot={null} />
            <span className="rule-row__name wf-row__gone">
              {wf.name}
              <span className="wf-tag">deleted</span>
              {wf.restorable_until && <small className="wf-row__until">restorable until {when(wf.restorable_until)}</small>}
            </span>
            <button
              type="button"
              className="wf-button wf-row__act"
              aria-label={`Restore ${wf.name}`}
              disabled={restoring?.has(wf.id)}
              onClick={() => onRestore?.(wf)}
            >
              Restore
            </button>
            {onPurge && (
              <button
                type="button"
                className="wf-button wf-button--danger wf-row__act"
                aria-label={`Purge ${wf.name}`}
                onClick={() => onPurge(wf)}
              >
                Purge
              </button>
            )}
          </div>
        ))}
      {purgePanel}
      {onShowDeleted && (
        <button
          type="button"
          className="wf-button wf-list__deleted"
          aria-pressed={showDeleted}
          onClick={() => onShowDeleted(!showDeleted)}
        >
          Show deleted
        </button>
      )}
    </nav>
  );
}
