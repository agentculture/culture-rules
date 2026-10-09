import { useState, type ComponentProps } from "react";
import { Link } from "react-router-dom";
import type { FoldModel } from "../../fold/model";
import { MachineDot, Switch, machineStyle } from "../../culture-design/stages";
import { AboutButton } from "../../components/AboutButton";
import type { WorkflowList as OriginalWorkflowList } from "../WorkflowList";
import { ChainView } from "./ChainView";
import { chainName, count, EndSummary, EntrySummary, RunSummary, workflowName, workflowUrl } from "./presentation";
import "./list.css";

/** t8 can retain the old props and add the read-only fold model from its loaded rules. */
export type WorkflowListProps = ComponentProps<typeof OriginalWorkflowList> & { model: FoldModel };

export function WorkflowList(props: Readonly<WorkflowListProps>) {
  const { model, workflows, selectedId, onOpen, onNew, newRef, onToggle, pending, slotOf } = props;
  const [chainId, setChainId] = useState<string | null>(null);
  const chain = model.chains.find((item) => item.workflowIds[0] === chainId);
  if (chain) return <section className="fold-list" aria-label="Workflows">
    <button type="button" className="wf-button" onClick={() => setChainId(null)}>List</button>
    <h2>{chainName(model, chain)}</h2><ChainView model={model} chain={chain} onOpen={onOpen} selectedId={selectedId} />
  </section>;
  return <nav className="fold-list" aria-label="Workflows">
    <button ref={newRef} type="button" className="rule-list__new" onClick={onNew}>New workflow</button>
    {!model.chains.length && <p>No workflows yet.</p>}
    {model.chains.map((item) => <article className="fold-card" key={item.workflowIds[0]} aria-label={`Chain: ${chainName(model, item)}`}>
      <header className="fold-card__header"><div><h2>{chainName(model, item)}</h2>
        <p>{count(item.workflowIds.length, "workflow")} · {count(item.entryPoints.length, "entry point")} · was {count(item.entryPoints.length + item.continuations.length, "rule")}</p>
      </div><button className="wf-button" type="button" onClick={() => setChainId(item.workflowIds[0])}>See it as one chain</button></header>
      {item.workflowIds.map((id) => {
        const folded = model.workflows.find((wf) => wf.id === id)!;
        const def = workflows.find((wf) => wf.id === id);
        const name = workflowName(model, id);
        const outgoing = model.continuations.filter((entry) => entry.fromWorkflowId === id);
        return <section className="fold-workflow" key={id} role="group" aria-label={`Workflow: ${name}`} data-workflow-id={id}>
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
            <dt>Starts when</dt><dd>{folded.entries.length ? folded.entries.map((entry) => <EntrySummary key={entry.rule.id} entry={entry} onOpen={onOpen} />) : "No entry points"}</dd>
            <dt>Continues into</dt><dd>{outgoing.length ? outgoing.map((entry) => <div className="fold-entry" key={entry.rule.id}>
              <Link to={workflowUrl(entry.workflowId, entry.rule.id)} onClick={() => onOpen?.(entry.workflowId)}>{workflowName(model, entry.workflowId)}</Link>
              <EntrySummary entry={entry} onOpen={onOpen} />
            </div>) : "No explicit continuation links."}</dd>
            <dt>Runs</dt><dd><RunSummary workflow={folded} /></dd>
            <dt>Ends with</dt><dd><EndSummary workflow={folded} /></dd>
          </dl>
        </section>;
      })}
    </article>)}
    {props.showDeleted && props.deleted?.map((wf) => <div className="fold-workflow" key={wf.id}>
      <span>{wf.name} · deleted{wf.restorable_until ? ` · restorable until ${wf.restorable_until.slice(0, 10)}` : ""}</span>
      <button type="button" className="wf-button" disabled={props.restoring?.has(wf.id)} onClick={() => props.onRestore?.(wf)}>Restore {wf.name}</button>
      {props.onPurge && <button type="button" className="wf-button" onClick={() => props.onPurge?.(wf)}>Purge {wf.name}</button>}
    </div>)}
    {props.purgePanel}
    {props.onShowDeleted && <button type="button" className="wf-button" aria-pressed={props.showDeleted ?? false} onClick={() => props.onShowDeleted?.(!props.showDeleted)}>Show deleted</button>}
  </nav>;
}
