import { useId } from "react";
import { Link } from "react-router-dom";
import type { Chain, FoldModel } from "../../fold/model";
import { count, SameEventNote, EndSummary, EntrySummary, fromText, guardText, triggerText, workflowName, workflowUrl } from "./presentation";
import "./list.css";

export interface ChainViewProps {
  model: FoldModel;
  /** Omit to show all components, including isolated workflows. */
  chain?: Chain;
  selectedId?: string | null;
  onOpen?: (id: string) => void;
}

/** A read-only, scrollable directed graph. Coordinates depend only on model order/counts. */
export function ChainView({ model, chain, selectedId, onOpen }: Readonly<ChainViewProps>) {
  const arrowId = useId().replaceAll(":", "");
  const ids = chain?.workflowIds ?? model.workflows.map((wf) => wf.id);
  const entries = chain?.entryPoints ?? model.entryPoints;
  const continuations = chain?.continuations ?? model.continuations;
  const positions = new Map(ids.map((id, index) => [id, { x: 360 + index * 340, y: 60 }]));
  const width = Math.max(680, 380 + ids.length * 340);
  const laneStart = Math.max(320, 60 + entries.length * 180);
  const linked = continuations.filter((entry) => entry.fromWorkflowId !== null && positions.has(entry.fromWorkflowId));
  const height = laneStart + linked.length * 100 + 40;
  const unresolved = continuations.filter((entry) => !linked.includes(entry));
  return <section className="fold-chain" aria-label="Chain view">
    <p>{count(entries.length + continuations.length + (chain ? 0 : model.d7Candidates.length), "rule")} → {count(entries.length, "entry point")}, {count(continuations.length, "continuation")}, {count(ids.length, "workflow")}</p>
    {!ids.length ? <p>No workflows yet.</p> : <section className="fold-chain__scroll" aria-label="Workflow chain diagram">
      <div className="fold-chain__canvas" style={{ width, height }}>
        <strong className="fold-chain__when">When</strong>
        <svg width={width} height={height} className="fold-chain__edges" aria-label="Directed entry and continuation edges" role="img">
          <defs><marker id={arrowId} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker></defs>
          {entries.map((entry, index) => {
            const target = positions.get(entry.workflowId)!;
            const y = 130 + index * 180;
            return <path key={entry.rule.id} data-entry-id={entry.rule.id} d={`M 280 ${y} C 320 ${y}, 320 ${target.y + 80}, ${target.x} ${target.y + 80}`}
              markerEnd={`url(#${arrowId})`}><title>{entry.rule.name} → {workflowName(model, entry.workflowId)}</title></path>;
          })}
          {linked.map((entry, index) => {
            const source = positions.get(entry.fromWorkflowId!)!, target = positions.get(entry.workflowId)!;
            const lane = laneStart + index * 100;
            const sx = source.x + 180, tx = target.x + 80;
            return <path key={entry.rule.id} data-continuation-id={entry.rule.id} data-source={entry.fromWorkflowId} data-target={entry.workflowId}
              className="fold-chain__continuation" d={`M ${sx} 280 V ${lane} H ${tx} V 280`} markerEnd={`url(#${arrowId})`}>
              <title>{entry.rule.name}: {fromText(entry)} → {workflowName(model, entry.workflowId)}</title>
            </path>;
          })}
        </svg>
        {entries.map((entry, index) => <fieldset key={entry.rule.id} className="fold-chain__entry plain-group" aria-label={`Entry point: ${entry.rule.name}`} style={{ left: 0, top: 60 + index * 180 }}>
          <Link to={workflowUrl(entry.workflowId, entry.rule.id)} onClick={() => onOpen?.(entry.workflowId)}>{entry.rule.name}</Link>
          <small>{triggerText(entry.rule)}</small>{!entry.enabled && <span className="fold-tag">Disabled</span>}
          <SameEventNote model={model} entry={entry} />
          <small>was {entry.rule.id}</small>
        </fieldset>)}
        {ids.map((id) => {
          const workflow = model.workflows.find((wf) => wf.id === id)!;
          return <fieldset key={id} className="fold-chain__workflow plain-group" aria-label={`Workflow: ${workflowName(model, id)}`} style={{ left: positions.get(id)!.x, top: 60 }}>
            <h3><Link to={workflowUrl(id)} aria-current={id === selectedId ? "true" : undefined} onClick={() => onOpen?.(id)}>{workflowName(model, id)}</Link></h3>
            <small>{id}</small>
            {!workflow.workflow && <p>Workflow definition missing</p>}
            <small>{count(workflow.entries.filter((entry) => entry.kind === "entry").length, "entry point")}</small>
            <strong>Ends with</strong><div className="fold-chain__end"><EndSummary workflow={workflow} /></div>
          </fieldset>;
        })}
        {linked.map((entry, index) => <div key={entry.rule.id} className="fold-chain__edge-label" style={{ left: 360, top: laneStart + index * 100 + 8, width: width - 400 }}>
          <Link to={workflowUrl(entry.workflowId, entry.rule.id)} onClick={() => onOpen?.(entry.workflowId)}>{entry.rule.name}</Link>
          <small>{fromText(entry)} → {workflowName(model, entry.workflowId)} · {triggerText(entry.rule)}{!entry.enabled ? " · Disabled" : ""}</small>
          <SameEventNote model={model} entry={entry} />
          <small>was {entry.rule.id}</small>
        </div>)}
      </div>
    </section>}
    {unresolved.length > 0 && <section aria-label="Continuations without a visible predecessor"><h3>Other continuations</h3>
      {unresolved.map((entry) => <div key={entry.rule.id}><p>To {workflowName(model, entry.workflowId)}</p><EntrySummary entry={entry} onOpen={onOpen} /></div>)}
    </section>}
    {linked.some((entry) => entry.rule.condition) && <details className="fold-chain__guards"><summary>Continuation guards</summary>
      {linked.filter((entry) => entry.rule.condition).map((entry) => <div key={entry.rule.id}><strong>{entry.rule.name}</strong><p>{guardText(entry.rule.condition!)}</p></div>)}
    </details>}
  </section>;
}
