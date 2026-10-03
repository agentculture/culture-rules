/**
 * The two card shapes of the 'Chosen — Workflows' board:
 *
 *   step card — a 190px white card; a header tinted with its machine's color
 *               (machine name + enable switch), the step name (17px bold),
 *               then one 30px mono row per typed port: inputs on the left
 *               (hollow handles), outputs on the right (filled handles).
 *               A `logic` step has a dashed border, like the Rules board's
 *               Condition: it decides. Selected, it floats its toolbar
 *               (placement chip, edit, delete) above itself.
 *   io card   — the workflow's `in` / `out`: dashed, unshadowed, Fraunces
 *               title, hollow handles.
 */
import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import type { Port, Step } from "../api/workflows";
import { machineStyle, Switch } from "../culture-design/stages";
import type { StepRun } from "./model";

export interface StepNodeData extends Record<string, unknown> {
  step: Step;
  label: string;
  /** Machine palette slot; null = unresolved / neutral. */
  slot: number | null;
  /** Header text: the machine, or the placement in words when unresolved. */
  host: string;
  subtitle: string | null;
  placement: string;
  run: StepRun | null;
  onToggle: (id: string) => void;
  onPlacement: (id: string, trigger: HTMLElement) => void;
  onEdit: (id: string, trigger: HTMLElement) => void;
  onDelete: (id: string) => void;
}

export interface IoNodeData extends Record<string, unknown> {
  side: "in" | "out";
  ports: Port[];
}

export type StepNodeType = Node<StepNodeData, "step">;
export type IoNodeType = Node<IoNodeData, "io">;

const STATUS_ICON: Record<string, string> = {
  succeeded: "M5 12l5 5 9-10",
  failed: "M6 6l12 12M18 6 6 18",
  running: "M12 6v6l4 2",
  waiting: "M12 6v6l4 2",
  pending: "M12 6v6l4 2",
  skipped: "M5 12h14",
  cancelled: "M6 6l12 12",
};

function RunBadge({ run }: Readonly<{ run: StepRun }>) {
  const words = run.host ? `${run.status} on ${run.host}` : run.status;
  return (
    <div className="wf-card__run" data-run-status={run.status}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {run.status === "running" || run.status === "waiting" || run.status === "pending" ? (
          <circle cx="12" cy="12" r="9" />
        ) : null}
        <path d={STATUS_ICON[run.status] ?? STATUS_ICON.pending} />
      </svg>
      <span>{words}</span>
      {run.attempt > 1 ? <span className="wf-card__attempt">· try {run.attempt}</span> : null}
    </div>
  );
}

function PortRow({ port, side, filled }: Readonly<{ port: Port; side: "in" | "out"; filled: boolean }>) {
  const type = port.type ?? "any";
  return (
    <span
      className={`wf-port wf-port--${side}`}
      data-port={`${side}:${port.name}`}
      data-port-type={type}
      title={`${port.name}: ${type}`}
    >
      {port.name}
      <Handle
        type={side === "in" ? "target" : "source"}
        position={side === "in" ? Position.Left : Position.Right}
        id={port.name}
        className={`wf-handle${filled ? " wf-handle--filled" : ""}`}
        data-port-type={type}
      />
    </span>
  );
}

export function StepNode({ id, data, selected }: NodeProps<StepNodeType>) {
  const { step, label, slot, host, subtitle, placement, run } = data;
  const enabled = step.enabled !== false;
  const runClass = run ? ` is-run-${run.status}` : "";
  return (
    <>
      {selected ? (
        <div className="wf-toolbar nodrag nopan" style={machineStyle(slot)}>
          <button
            type="button"
            className="wf-toolbar__chip"
            data-machine-slot={slot ?? "none"}
            aria-haspopup="dialog"
            onClick={(e) => data.onPlacement(id, e.currentTarget)}
          >
            {placement} <span aria-hidden="true">▾</span>
          </button>
          <button
            type="button"
            className="wf-toolbar__button"
            aria-label={`Edit ${label}`}
            aria-haspopup="dialog"
            onClick={(e) => data.onEdit(id, e.currentTarget)}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M4 20h4L19 9l-4-4L4 16v4z" />
            </svg>
          </button>
          <button
            type="button"
            className="wf-toolbar__button wf-toolbar__button--danger"
            aria-label={`Delete ${label}`}
            onClick={() => data.onDelete(id)}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
            </svg>
          </button>
        </div>
      ) : null}
      <div
        className={`wf-card wf-card--${step.kind}${enabled ? "" : " is-disabled"}${runClass}`}
        style={machineStyle(slot)}
      >
        <div className="wf-card__machine" data-machine-slot={slot ?? "none"}>
          <span className="wf-card__host" title={placement}>
            {host}
          </span>
          <span className="nodrag">
            <Switch label={`${label} enabled`} checked={enabled} onChange={() => data.onToggle(id)} />
          </span>
        </div>
        <div className="wf-card__title">
          <span className="wf-card__name">{label}</span>
          {subtitle ? <span className="wf-card__sub">{subtitle}</span> : null}
        </div>
        {run ? <RunBadge run={run} /> : null}
        <div className="wf-card__ports">
          {(step.inputs ?? []).map((p) => (
            <PortRow key={`in-${p.name}`} port={p} side="in" filled={false} />
          ))}
          {(step.outputs ?? []).map((p) => (
            <PortRow key={`out-${p.name}`} port={p} side="out" filled />
          ))}
        </div>
      </div>
    </>
  );
}

export function IoNode({ data }: NodeProps<IoNodeType>) {
  // The workflow's inputs are *sources* on the canvas; its outputs are *targets*.
  const side = data.side === "in" ? "out" : "in";
  return (
    <div className={`wf-card wf-card--io wf-card--${data.side}`}>
      <div className="wf-card__io-title">{data.side}</div>
      <div className="wf-card__ports wf-card__ports--io">
        {data.ports.map((p) => (
          <PortRow key={p.name} port={p} side={side} filled={false} />
        ))}
      </div>
    </div>
  );
}

export const NODE_TYPES = { step: StepNode, io: IoNode };
