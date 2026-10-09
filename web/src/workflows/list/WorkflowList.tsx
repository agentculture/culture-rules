import { useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { Link } from "react-router-dom";
import type { FoldModel } from "../../fold/model";
import { MachineDot, Switch, machineStyle } from "../../culture-design/stages";
import { AboutButton } from "../../components/AboutButton";
import type { WorkflowDef } from "../../api/workflows";
import { ChainView } from "./ChainView";
import { chainName, count, EndSummary, EntrySummary, RunSummary, workflowName, workflowUrl } from "./presentation";
import "./list.css";

export interface WorkflowListProps {
  model: FoldModel;
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

export function WorkflowList(props: Readonly<WorkflowListProps>) {
  const { model, workflows, selectedId, onOpen, onNew, newRef, onToggle, pending, slotOf } = props;
  const [chainId, setChainId] = useState<string | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const cardButtons = useRef(new Map<string, HTMLButtonElement>());
  const returnTo = useRef<string | null>(null);
  useLayoutEffect(() => {
    if (chainId !== null) headingRef.current?.focus();
    else if (returnTo.current !== null) {
      cardButtons.current.get(returnTo.current)?.focus();
      returnTo.current = null;
    }
  }, [chainId]);
  const chain = model.chains.find((item) => item.workflowIds[0] === chainId);
  if (chain) return <section className="fold-list" aria-label="Workflows">
    <header className="fold-card__header">
      <h2 ref={headingRef} tabIndex={-1}>{chainName(model, chain)}</h2>
      <div className="fold-view-toggle" role="group" aria-label="Workflows view">
        <button type="button" className="wf-button" aria-pressed={false} onClick={() => setChainId(null)}>List</button>
        <button type="button" className="wf-button" aria-pressed={true}>Chain</button>
      </div>
    </header><ChainView model={model} chain={chain} onOpen={onOpen} selectedId={selectedId} />
  </section>;
  return <nav className="fold-list" aria-label="Workflows">
    <button ref={newRef} type="button" className="rule-list__new" onClick={onNew}>New workflow</button>
    <p>{count(model.workflows.length, "workflow")} · {count(model.entryPoints.length, "entry point")} · was {count(model.entryPoints.length + model.continuations.length + model.d7Candidates.length, "rule")}</p>
    {!model.chains.length && <p>No workflows yet.</p>}
    {model.chains.map((item) => <article className="fold-card" key={item.workflowIds[0]} aria-label={`Chain: ${chainName(model, item)}`}>
      <header className="fold-card__header"><div><h2>{chainName(model, item)}</h2>
        <p>{count(item.workflowIds.length, "workflow")} linked by continuations · {count(item.entryPoints.length, "entry point")} · was {count(item.entryPoints.length + item.continuations.length, "rule")}</p>
      </div><button className="wf-button" type="button" ref={(button) => {
        if (button) cardButtons.current.set(item.workflowIds[0], button);
        else cardButtons.current.delete(item.workflowIds[0]);
      }} onClick={() => {
        returnTo.current = item.workflowIds[0];
        setChainId(item.workflowIds[0]);
      }}>See it as one chain</button></header>
      {item.workflowIds.map((id) => {
        const folded = model.workflows.find((wf) => wf.id === id)!;
        const def = workflows.find((wf) => wf.id === id);
        const name = workflowName(model, id);
        const outgoing = model.continuations.filter((entry) => entry.fromWorkflowId === id);
        return <section className={`fold-workflow${def?.enabled === false ? " is-disabled" : ""}`} key={id} role="group" aria-label={`Workflow: ${name}`} data-workflow-id={id} data-missing={folded.workflow ? undefined : "true"}>
          <header className="fold-workflow__header" style={machineStyle(def ? slotOf(def) : null)}>
            <MachineDot slot={def ? slotOf(def) : null} /><div>
              <h3><Link to={workflowUrl(id)} aria-current={id === selectedId ? "true" : undefined} onClick={() => onOpen?.(id)}>{name}</Link></h3>
              <small>{id}{def?.version !== undefined ? ` · v${def.version}` : ""}</small>
              {!folded.workflow && <p>Workflow definition missing</p>}
              {def?.steps && <p>{count(def.steps.length, "step")}{def.steps.length ? `: ${def.steps.map((step) => step.name || step.id).join(" → ")}` : ""}</p>}
            </div>
            {def && <><AboutButton noun="workflows" id={id} name={name} /><Switch label={`${name} enabled`} checked={def.enabled !== false} disabled={pending?.has(id)} onChange={() => onToggle(def)} /></>}
          </header>
          <dl className="fold-summary">
            <dt>Starts when</dt><dd>{folded.entries.length ? folded.entries.map((entry) => <EntrySummary model={model} key={entry.rule.id} entry={entry} onOpen={onOpen} />) : "No entry points"}</dd>
            <dt>Continues into</dt><dd>{outgoing.length ? outgoing.map((entry) => <div className="fold-entry" key={entry.rule.id}>
              <span>{workflowName(model, entry.workflowId)}</span>
              <EntrySummary entry={entry} onOpen={onOpen} />
            </div>) : "Nothing. The chain ends here."}</dd>
            <dt>Runs</dt><dd><RunSummary workflow={folded} /></dd>
            <dt>Ends with</dt><dd><EndSummary workflow={folded} /></dd>
          </dl>
        </section>;
      })}
    </article>)}
    {model.d7Candidates.length > 0 && <section aria-label="Rules without a workflow">
      <h2>Rules without a workflow</h2>
      {model.d7Candidates.map((rule) => <div className="fold-entry" key={rule.id}>
        <Link to={`/workflows?entry=${encodeURIComponent(rule.id)}`}>{rule.name}</Link>
        <small>D7 candidate · can get a workflow of its own, with no steps yet</small>
      </div>)}
    </section>}
    {props.showDeleted && props.deleted?.map((wf) => <div className="fold-workflow is-deleted" key={wf.id} data-workflow-id={wf.id}>
      <span className="fold-workflow__name">{wf.name}</span> <small>deleted</small>
      {wf.restorable_until ? <small> · restorable until {wf.restorable_until.slice(0, 10)}</small> : null}
      <button type="button" className="wf-button" disabled={props.restoring?.has(wf.id)} onClick={() => props.onRestore?.(wf)}>Restore {wf.name}</button>
      {props.onPurge && <button type="button" className="wf-button" onClick={() => props.onPurge?.(wf)}>Purge {wf.name}</button>}
    </div>)}
    {props.purgePanel}
    {props.onShowDeleted && <button type="button" className="wf-button" aria-pressed={props.showDeleted ?? false} onClick={() => props.onShowDeleted?.(!props.showDeleted)}>Show deleted</button>}
  </nav>;
}
