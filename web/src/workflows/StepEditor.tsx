import { PORT_TYPES, STEP_KINDS, type Port, type PortType, type StepKind, type WorkflowDef } from "../api/workflows";
import {
  compatibleSources,
  connect,
  disconnect,
  stepLabel,
  updateStep,
} from "./model";
import Panel from "./Panel";

/**
 * Edit one step: name, kind, typed ports, and — the keyboard path to wiring
 * — where each input comes from, offering only type-compatible sources.
 * Every change applies to the draft at once; Done closes.
 */
export function StepEditor({
  workflow,
  stepId,
  returnFocus,
  onChange,
  onClose,
}: Readonly<{
  workflow: WorkflowDef;
  stepId: string;
  returnFocus?: HTMLElement | null;
  onChange: (wf: WorkflowDef) => void;
  onClose: () => void;
}>) {
  const step = (workflow.steps ?? []).find((s) => s.id === stepId);
  if (!step) return null;
  const label = stepLabel(step);
  const inputs = step.inputs ?? [];
  const outputs = step.outputs ?? [];

  const setPorts = (side: "inputs" | "outputs", ports: Port[]) =>
    onChange(updateStep(workflow, stepId, { [side]: ports }));
  const patchPort = (side: "inputs" | "outputs", i: number, patch: Partial<Port>) => {
    const ports = side === "inputs" ? inputs : outputs;
    setPorts(side, ports.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  };
  const addPort = (side: "inputs" | "outputs") => {
    const ports = side === "inputs" ? inputs : outputs;
    const taken = new Set(ports.map((p) => p.name));
    let n = ports.length + 1;
    while (taken.has(`${side === "inputs" ? "in" : "out"}${n}`)) n += 1;
    setPorts(side, [...ports, { name: `${side === "inputs" ? "in" : "out"}${n}`, type: "any" }]);
  };
  const wiredFrom = (port: string) => {
    const e = (workflow.edges ?? []).find((x) => x.target === stepId && x.target_port === port);
    return e ? `${e.source}\u0000${e.source_port}` : "";
  };
  const wire = (port: string, value: string) => {
    if (!value) return onChange(disconnect(workflow, stepId, port));
    const [source, sourcePort] = value.split("\u0000");
    const res = connect(workflow, { source, sourcePort, target: stepId, targetPort: port });
    if (res.ok) onChange(res.workflow);
  };

  const portRows = (side: "inputs" | "outputs") => {
    const ports = side === "inputs" ? inputs : outputs;
    const word = side === "inputs" ? "input" : "output";
    return (
      <fieldset className="wf-ports">
        <legend className="wf-ports__legend">{side === "inputs" ? "Inputs" : "Outputs"}</legend>
        {ports.length === 0 ? <p className="wf-ports__empty">None yet.</p> : null}
        {ports.map((p, i) => (
          <div className="wf-ports__row" key={`${side}-${i}`}>
            <input
              type="text"
              className="wf-ports__name"
              aria-label={`Name of ${word} ${p.name}`}
              value={p.name}
              onChange={(e) => patchPort(side, i, { name: e.target.value })}
            />
            <select
              aria-label={`Type of ${word} ${p.name}`}
              value={p.type ?? "any"}
              onChange={(e) => patchPort(side, i, { type: e.target.value as PortType })}
            >
              {PORT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            {side === "inputs" ? (
              <select
                aria-label={`${p.name} comes from`}
                className="wf-ports__wire"
                value={wiredFrom(p.name)}
                onChange={(e) => wire(p.name, e.target.value)}
              >
                <option value="">Not wired</option>
                {compatibleSources(workflow, stepId, p.name).map((o) => (
                  <option key={`${o.source}.${o.port}`} value={`${o.source}\u0000${o.port}`}>
                    {o.label}
                  </option>
                ))}
              </select>
            ) : null}
            <button
              type="button"
              className="wf-ports__remove"
              aria-label={`Remove ${word} ${p.name}`}
              onClick={() => setPorts(side, ports.filter((_, j) => j !== i))}
            >
              ×
            </button>
          </div>
        ))}
        <button type="button" className="wf-button wf-button--small" onClick={() => addPort(side)}>
          Add {word}
        </button>
      </fieldset>
    );
  };

  return (
    <Panel label={`Edit ${label}`} onClose={onClose} returnFocus={returnFocus} className="wf-panel--wide">
      <div className="wf-form">
        <h2 className="wf-panel__title">{label}</h2>
        <div className="wf-form__pair">
          <label className="wf-field">
            <span>Name</span>
            <input
              type="text"
              value={step.name ?? ""}
              onChange={(e) => onChange(updateStep(workflow, stepId, { name: e.target.value }))}
            />
          </label>
          <label className="wf-field">
            <span>Kind</span>
            <select
              value={step.kind}
              onChange={(e) => {
                const kind = e.target.value as StepKind;
                const loop = kind === "for_each" || kind === "retry_until";
                onChange(
                  updateStep(workflow, stepId, {
                    kind,
                    max_iterations: loop ? (step.max_iterations ?? 1) : step.max_iterations,
                  }),
                );
              }}
            >
              {STEP_KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </label>
        </div>
        {portRows("inputs")}
        {portRows("outputs")}
        <div className="wf-form__actions">
          <button type="button" className="wf-button wf-button--primary" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </Panel>
  );
}

export default StepEditor;
