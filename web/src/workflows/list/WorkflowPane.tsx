import { useState, type KeyboardEvent, type RefObject } from "react";
import { Link } from "react-router-dom";
import type { WorkflowDef } from "../../api/workflows";
import { workflowUrl } from "./presentation";
import "./pane.css";

export interface WorkflowPaneProps {
  workflows: readonly WorkflowDef[];
  selectedId: string | null;
  collapsed: boolean;
  onToggle: () => void;
  onNew: () => void;
  onNewRule?: () => void;
  newRef?: RefObject<HTMLButtonElement>;
  newRuleRef?: RefObject<HTMLButtonElement>;
  /** A row's link was followed. */
  onOpen?: (id: string) => void;
}

const nameOf = (wf: WorkflowDef) => wf.name || wf.id;

/**
 * The compact left pane while a workflow is open: a search box, then workflow names only, one
 * short row each. It collapses to a button-width rail holding only its toggle. Chain cards,
 * counts, deleted workflows and rules without a workflow live in the overview at /workflows.
 */
export function WorkflowPane(props: Readonly<WorkflowPaneProps>) {
  const { workflows, selectedId, collapsed, onToggle, onOpen } = props;
  const [query, setQuery] = useState("");
  const toggle = (
    <button
      type="button"
      className="wf-pane__toggle"
      aria-expanded={!collapsed}
      aria-label={collapsed ? "Show workflows" : "Hide workflows"}
      title={collapsed ? "Show workflows" : "Hide workflows"}
      onClick={onToggle}
    >
      <span aria-hidden="true">{collapsed ? "»" : "«"}</span>
    </button>
  );
  const needle = query.trim().toLowerCase();
  const shown = needle ? workflows.filter((wf) => nameOf(wf).toLowerCase().includes(needle)) : workflows;
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape" && query) {
      e.preventDefault();
      setQuery("");
    }
  };
  return (
    <aside className={collapsed ? "wf-pane wf-pane--rail" : "wf-pane"}>
      <div className="wf-pane__top">
        {toggle}
        {collapsed ? null : (
          <button ref={props.newRef} type="button" className="wf-pane__small" onClick={props.onNew}>
            New workflow
          </button>
        )}
        {collapsed || !props.onNewRule ? null : (
          <button ref={props.newRuleRef} type="button" className="wf-pane__small" onClick={props.onNewRule}>
            New rule
          </button>
        )}
      </div>
      {collapsed ? null : (
      <div id="wf-pane-body" className="wf-pane__body">
        <input
          type="search"
          className="wf-pane__search"
          aria-label="Search workflows"
          placeholder="Search workflows"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <nav aria-label="Workflows">
          <ul className="wf-pane__list">
            {shown.map((wf) => (
              <li key={wf.id}>
                <Link
                  className={`wf-pane__row${wf.enabled === false ? " is-disabled" : ""}`}
                  to={workflowUrl(wf.id)}
                  aria-current={wf.id === selectedId ? "page" : undefined}
                  onClick={() => onOpen?.(wf.id)}
                >
                  {nameOf(wf)}
                </Link>
              </li>
            ))}
          </ul>
          {shown.length === 0 ? <p className="wf-pane__none">{needle ? "No match" : "No workflows yet"}</p> : null}
        </nav>
      </div>
      )}
    </aside>
  );
}
