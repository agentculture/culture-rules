import { useState, type ReactNode } from "react";
import GuidedNotice from "../components/GuidedNotice";
import {
  PORT_TYPES,
  STEP_KINDS,
  type Actor,
  type Port,
  type PortType,
  type Step,
  type StepKind,
  type WorkflowDef,
} from "../api/workflows";
import {
  compatibleSources,
  connect,
  disconnect,
  stepLabel,
  updateStep,
} from "./model";
import Panel from "./Panel";
import {
  RETRY_FIELDS,
  isScalar,
  parseConfigJson,
  parseNumber,
  parseTimeout,
  runnerCommands,
  withRetryField,
  type Config,
  type Parsed,
} from "./stepFields";

type ErrorCode = "invalid_value" | "range" | "malformed" | "duplicate";

/** A numeric text field: invalid text stays local, shows a guided error, and never reaches the draft. */
function NumberField({
  label,
  hint,
  value,
  parse,
  onValid,
  onError,
}: Readonly<{
  label: string;
  hint: string;
  value: number | null | undefined;
  parse: (text: string) => Parsed;
  onValid: (value: number | null) => void;
  onError: (code: ErrorCode | null) => void;
}>) {
  const [text, setText] = useState(value == null ? "" : String(value));
  const [code, setCode] = useState<ErrorCode | null>(null);
  const hintId = `hint-${label.replace(/\W+/g, "-")}`;
  return (
    <div className="wf-field">
      <label className="wf-field">
        <span>{label}</span>
        <input
          type="text"
          inputMode="decimal"
          value={text}
          aria-invalid={code ? true : undefined}
          aria-describedby={code ? hintId : undefined}
          onChange={(e) => {
            setText(e.target.value);
            const r = parse(e.target.value);
            setCode(r.ok ? null : r.code);
            onError(r.ok ? null : r.code);
            if (r.ok) onValid(r.value);
          }}
        />
      </label>
      {code ? (
        <div className="wf-field__error">
          <GuidedNotice code={code} />
          <p id={hintId} className="wf-field__hint">
            {label} needs {hint}. Leave it empty to leave it unset.
          </p>
        </div>
      ) : null}
    </div>
  );
}

/** Advanced fields stay folded until asked for (or until they already hold a value). */
function Disclosure({ title, startOpen, children }: Readonly<{ title: string; startOpen: boolean; children: ReactNode }>) {
  const [open, setOpen] = useState(startOpen);
  return (
    <details className="wf-advanced" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className="wf-advanced__summary">{title}</summary>
      <div className="wf-advanced__body">{children}</div>
    </details>
  );
}

interface Row {
  /** Stable React key: rows are editable, so neither the position nor the (editable) key will do. */
  id: number;
  key: string;
  text: string;
  value: unknown;
}

let nextRowId = 0;

const rowsOf = (config: Config): Row[] =>
  Object.entries(config).map(([key, value]) => ({
    id: ++nextRowId,
    key,
    value,
    text: isScalar(value) ? String(value ?? "") : JSON.stringify(value),
  }));

/** A typed edit keeps the type the value already had (number stays number). */
const retype = (orig: unknown, text: string): unknown => {
  if (typeof orig === "number" && text.trim() !== "" && Number.isFinite(Number(text))) return Number(text);
  if (typeof orig === "boolean" && (text === "true" || text === "false")) return text === "true";
  return text;
};

/** Key/value config for steps with no runner command list; objects and lists are read-only here. */
function ConfigPairs({
  config,
  onConfig,
  onError,
}: Readonly<{ config: Config; onConfig: (c: Config) => void; onError: (code: ErrorCode | null) => void }>) {
  const [rows, setRows] = useState<Row[]>(() => rowsOf(config));
  const [dup, setDup] = useState(false);
  const commit = (next: Row[]) => {
    setRows(next);
    const keys = next.map((r) => r.key).filter((k) => k !== "");
    const dupe = new Set(keys).size !== keys.length;
    setDup(dupe);
    onError(dupe ? "duplicate" : null);
    if (dupe) return;
    onConfig(Object.fromEntries(next.filter((r) => r.key !== "").map((r) => [r.key, r.value])));
  };
  const name = (r: Row, i: number) => (r.key === "" ? String(i + 1) : r.key);
  return (
    <div className="wf-pairs">
      {rows.length === 0 ? <p className="wf-ports__empty">No settings yet.</p> : null}
      {rows.map((r, i) => (
        <div className="wf-pairs__row" key={r.id}>
          <input
            type="text"
            className="wf-ports__name"
            aria-label={`Config key ${i + 1}`}
            value={r.key}
            onChange={(e) => commit(rows.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))}
          />
          <input
            type="text"
            aria-label={`Config value ${name(r, i)}`}
            value={r.text}
            readOnly={!isScalar(r.value)}
            title={isScalar(r.value) ? undefined : "Nested values are edited as JSON"}
            onChange={(e) =>
              commit(rows.map((x, j) => (j === i ? { ...x, text: e.target.value, value: retype(x.value, e.target.value) } : x)))
            }
          />
          <button
            type="button"
            className="wf-ports__remove"
            aria-label={`Remove config entry ${name(r, i)}`}
            onClick={() => commit(rows.filter((_, j) => j !== i))}
          >
            ×
          </button>
        </div>
      ))}
      {dup ? <GuidedNotice code="duplicate" /> : null}
      <button
        type="button"
        className="wf-button wf-button--small"
        onClick={() => setRows([...rows, { id: ++nextRowId, key: "", text: "", value: "" }])}
      >
        Add config entry
      </button>
    </div>
  );
}

/** Raw JSON for config: the advanced mode, never the default. */
function ConfigJson({
  config,
  onConfig,
  onError,
}: Readonly<{ config: Config; onConfig: (c: Config) => void; onError: (code: ErrorCode | null) => void }>) {
  const [text, setText] = useState(JSON.stringify(config, null, 2));
  const [bad, setBad] = useState(false);
  return (
    <div className="wf-field">
      <label className="wf-field">
        <span>Config JSON</span>
        <textarea
          className="wf-json"
          rows={8}
          spellCheck={false}
          value={text}
          aria-invalid={bad ? true : undefined}
          onChange={(e) => {
            setText(e.target.value);
            const r = parseConfigJson(e.target.value);
            setBad(!r.ok);
            onError(r.ok ? null : "malformed");
            if (r.ok) onConfig(r.value);
          }}
        />
      </label>
      {bad ? <GuidedNotice code="malformed" /> : null}
    </div>
  );
}

/** An argument's current value as field text. */
const argText = (v: unknown): string => {
  if (v === undefined || v === null) return "";
  return typeof v === "string" ? v : JSON.stringify(v);
};

/** A numeric argument: the number typed, or undefined (unset) when blank or not a number. */
const numericArg = (t: string): number | undefined => {
  if (t.trim() === "" || !Number.isFinite(Number(t))) return undefined;
  return Number(t);
};

/** Runner steps: pick a registered command and fill its declared, typed arguments. */
function RunnerConfig({
  config,
  commands,
  onConfig,
}: Readonly<{
  config: Config;
  commands: NonNullable<ReturnType<typeof runnerCommands>>;
  onConfig: (c: Config) => void;
}>) {
  const current = typeof config.command === "string" ? config.command : "";
  const args = (config.args && typeof config.args === "object" ? config.args : {}) as Config;
  const declared = commands[current]?.params ?? {};
  const setCommand = (command: string) => {
    const { command: _drop, ...rest } = config;
    onConfig(command ? { ...rest, command } : rest);
  };
  const setArg = (param: string, value: unknown) => {
    const next = { ...args };
    if (value === undefined || value === "") delete next[param];
    else next[param] = value;
    onConfig({ ...config, args: next });
  };
  return (
    <div className="wf-form">
      <label className="wf-field">
        <span>Command</span>
        <select value={current} onChange={(e) => setCommand(e.target.value)}>
          <option value="">Not chosen</option>
          {current && !(current in commands) ? <option value={current}>{current} (not registered)</option> : null}
          {Object.keys(commands).map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </label>
      {Object.entries(declared).map(([param, type]) => {
        const v = args[param];
        if (type === "boolean") {
          return (
            <label className="wf-mode" key={param}>
              <input
                type="checkbox"
                checked={v === true}
                onChange={(e) => setArg(param, e.target.checked ? true : undefined)}
              />
              <span>{param}</span>
            </label>
          );
        }
        const numeric = type === "number" || type === "integer";
        return (
          <label className="wf-field" key={param}>
            <span>{param}</span>
            <input
              type={numeric ? "number" : "text"}
              step={type === "integer" ? 1 : undefined}
              value={argText(v)}
              onChange={(e) => setArg(param, numeric ? numericArg(e.target.value) : e.target.value)}
            />
          </label>
        );
      })}
    </div>
  );
}

/** The step's config editor: a runner's typed arguments, raw JSON on request, or key/value pairs. */
function ConfigEditor({
  config,
  commands,
  rawConfig,
  onConfig,
  onError,
}: Readonly<{
  config: Config;
  commands: ReturnType<typeof runnerCommands>;
  rawConfig: boolean;
  onConfig: (c: Config) => void;
  onError: (code: ErrorCode | null) => void;
}>) {
  if (commands && !rawConfig) return <RunnerConfig config={config} commands={commands} onConfig={onConfig} />;
  if (rawConfig) return <ConfigJson config={config} onConfig={onConfig} onError={onError} />;
  return <ConfigPairs config={config} onConfig={onConfig} onError={onError} />;
}

/**
 * Edit one step: name, kind, typed ports, and — the keyboard path to wiring
 * — where each input comes from, offering only type-compatible sources.
 * Every change applies to the draft at once; Done closes.
 */
export function StepEditor({
  workflow,
  stepId,
  actors = [],
  returnFocus,
  onChange,
  onClose,
}: Readonly<{
  workflow: WorkflowDef;
  stepId: string;
  /** Known actors; a runner placement offers that runner's commands. */
  actors?: readonly Actor[];
  returnFocus?: HTMLElement | null;
  onChange: (wf: WorkflowDef) => void;
  onClose: () => void;
}>) {
  const [errors, setErrors] = useState<Record<string, ErrorCode | null>>({});
  const [rawConfig, setRawConfig] = useState(false);
  const step = (workflow.steps ?? []).find((s) => s.id === stepId);
  if (!step) return null;
  const setError = (field: string) => (code: ErrorCode | null) => setErrors((e) => ({ ...e, [field]: code }));
  const blocked = Object.values(errors).some(Boolean);
  const patch = (p: Partial<Omit<Step, "id">>) => onChange(updateStep(workflow, stepId, p));
  const config = step.config ?? {};
  const commands = runnerCommands(actors, step.placement?.actor);
  const loop = step.kind === "for_each" || step.kind === "retry_until";
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
            <label className="wf-ports__required">
              <input
                type="checkbox"
                aria-label={`${side === "inputs" ? "Input" : "Output"} ${p.name} is required`}
                checked={p.required !== false}
                onChange={(e) => patchPort(side, i, { required: e.target.checked })}
              />
              <span aria-hidden="true">Required</span>
            </label>
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
        <label className="wf-field">
          <span>Description</span>
          <textarea
            rows={2}
            value={step.description ?? ""}
            onChange={(e) => patch({ description: e.target.value })}
          />
        </label>
        {portRows("inputs")}
        {portRows("outputs")}
        {loop ? (
          <NumberField
            label="Max iterations"
            hint="a whole number, 1 or more"
            value={step.max_iterations}
            parse={(t) => parseNumber(t, { integer: true, min: 1 })}
            onValid={(v) => patch({ max_iterations: v })}
            onError={setError("max_iterations")}
          />
        ) : null}
        <Disclosure
          title="Timeout and retries"
          startOpen={step.timeout_s != null || step.retry != null}
        >
          <NumberField
            label="Timeout (seconds)"
            hint="a number greater than 0"
            value={step.timeout_s}
            parse={parseTimeout}
            onValid={(v) => patch({ timeout_s: v })}
            onError={setError("timeout_s")}
          />
          <div className="wf-form__trio">
            {RETRY_FIELDS.map((f) => (
              <NumberField
                key={f.key}
                label={f.label}
                hint={f.hint}
                value={step.retry?.[f.key]}
                parse={(t) => parseNumber(t, f.opts)}
                onValid={(v) => patch({ retry: withRetryField(step.retry, f.key, v) })}
                onError={setError(f.key)}
              />
            ))}
          </div>
        </Disclosure>
        <Disclosure title="Configuration" startOpen={Object.keys(config).length > 0}>
          <ConfigEditor
            config={config}
            commands={commands}
            rawConfig={rawConfig}
            onConfig={(c) => patch({ config: c })}
            onError={setError("config")}
          />
          <button
            type="button"
            className="wf-button wf-button--small"
            onClick={() => {
              setErrors((e) => ({ ...e, config: null }));
              setRawConfig(!rawConfig);
            }}
          >
            {rawConfig ? "Back to fields" : "Edit config as JSON"}
          </button>
        </Disclosure>
        <div className="wf-form__actions">
          <button type="button" className="wf-button wf-button--primary" disabled={blocked} onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </Panel>
  );
}

export default StepEditor;
