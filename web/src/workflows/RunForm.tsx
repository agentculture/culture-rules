import { useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import GuidedNotice from "../components/GuidedNotice";
import { ApiError } from "../api/client";
import { runWorkflow, type Port, type PortType, type RunDoc, type WorkflowDef } from "../api/workflows";
import Panel from "./Panel";

export type Parsed = { ok: true; value: unknown } | { ok: false; reason: string };

const fail = (reason: string): Parsed => ({ ok: false, reason });

const isPlainObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

function parseNumber(type: "number" | "integer", raw: string): Parsed {
  const n = Number(raw.trim());
  if (raw.trim() === "" || !Number.isFinite(n)) return fail("Enter a number.");
  if (type === "integer" && !Number.isInteger(n)) return fail("Enter a whole number, no decimals.");
  return { ok: true, value: n };
}

function parseContainer(type: "object" | "array", raw: string): Parsed {
  let v: unknown;
  try {
    v = JSON.parse(raw);
  } catch {
    return fail("Enter valid JSON.");
  }
  if (type === "object") return isPlainObject(v) ? { ok: true, value: v } : fail('Enter a JSON object, like {"key": 1}.');
  return Array.isArray(v) ? { ok: true, value: v } : fail("Enter a JSON array, like [1, 2].");
}

/** A raw field value as the JSON value of its port type, or why it cannot be one. */
export function parseField(type: PortType, raw: string): Parsed {
  switch (type) {
    case "string":
      return { ok: true, value: raw };
    case "number":
    case "integer":
      return parseNumber(type, raw);
    case "object":
    case "array":
      return parseContainer(type, raw);
    case "boolean":
      return { ok: true, value: raw === "true" };
    default:
      try {
        return { ok: true, value: JSON.parse(raw) };
      } catch {
        return { ok: true, value: raw };
      }
  }
}

const typeOf = (p: Port): PortType => p.type ?? "any";
/** The model's `required` defaults to true. */
const isRequired = (p: Port) => p.required !== false;

/**
 * The only default the model offers: ports carry none, so a same-named
 * workflow variable's default (when it has one) fills the field.
 */
function initialValue(wf: WorkflowDef, p: Port): string {
  const v = (wf.variables ?? []).find((x) => x.name === p.name);
  const d = v?.default;
  if (d === undefined || d === null) return "";
  return typeof d === "string" ? d : JSON.stringify(d, null, 2);
}

const placeholderFor = (type: PortType) => {
  if (type === "object") return "{ }";
  return type === "array" ? "[ ]" : "JSON or text";
};

const stepFor = (type: PortType): number | "any" | undefined => {
  if (type === "integer") return 1;
  return type === "number" ? "any" : undefined;
};

function Control({
  port,
  id,
  value,
  invalid,
  describedBy,
  onChange,
}: Readonly<{
  port: Port;
  id: string;
  value: string;
  invalid: boolean;
  describedBy?: string;
  onChange: (v: string) => void;
}>) {
  const type = typeOf(port);
  const required = isRequired(port) && type !== "boolean";
  const common = {
    id,
    "aria-invalid": invalid || undefined,
    "aria-describedby": describedBy,
    required,
    "aria-required": required || undefined,
  };
  if (type === "boolean") {
    return (
      <input
        {...common}
        type="checkbox"
        checked={value === "true"}
        onChange={(e) => onChange(e.target.checked ? "true" : "false")}
      />
    );
  }
  if (type === "object" || type === "array" || type === "any") {
    return (
      <textarea
        {...common}
        rows={type === "any" ? 2 : 4}
        spellCheck={false}
        value={value}
        placeholder={placeholderFor(type)}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  const numeric = type === "number" || type === "integer";
  const step = stepFor(type);
  return (
    <input
      {...common}
      type={numeric ? "number" : "text"}
      step={step}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

/** Each port's raw text as its JSON value, or the reason it cannot be one. */
function collectInputs(ports: Port[], values: Record<string, string>) {
  const inputs: Record<string, unknown> = {};
  const problems: Record<string, string> = {};
  for (const p of ports) {
    const type = typeOf(p);
    const raw = values[p.name] ?? "";
    if (type !== "boolean" && raw.trim() === "") {
      if (isRequired(p)) problems[p.name] = "Required";
      continue;
    }
    const res = parseField(type, raw);
    if (res.ok) inputs[p.name] = res.value;
    else problems[p.name] = res.reason;
  }
  return { inputs, problems };
}

/** A failed run request split into per-field codes and, when none is field-specific, the error to show on top. */
function refusalOf(err: unknown, ports: Port[]): { codes: Record<string, string>; top: ApiError | null } {
  const e = err instanceof ApiError ? err : new ApiError(0, "unknown", String(err));
  const codes = fieldCodes(e, new Set(ports.map((p) => p.name)));
  return { codes, top: Object.keys(codes).length > 0 ? null : e };
}

/** The `inputs.<port>` field errors of a 422, as port name -> code. */
function fieldCodes(err: ApiError, names: Set<string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const e of err.errors) {
    const m = /^inputs\.([^.[\]]+)/.exec(e.path);
    if (m && names.has(m[1]) && !out[m[1]]) out[m[1]] = e.code;
  }
  return out;
}

function RunField({
  port: p,
  id,
  value,
  message: msg,
  code,
  onChange,
}: Readonly<{
  port: Port;
  id: string;
  value: string;
  message: string | undefined;
  code: string | undefined;
  onChange: (v: string) => void;
}>) {
  const type = typeOf(p);
  return (
    <div className="wf-run-form__field" data-field={p.name}>
      <div className="wf-run-form__head">
        <label htmlFor={id} className="wf-run-form__label">
          <span>{p.name}</span>
          {isRequired(p) ? <span aria-hidden="true"> *</span> : null}
        </label>
        <span className="wf-run-form__type">
          {type}
          {isRequired(p) ? ", required" : ", optional"}
        </span>
      </div>
      <Control
        port={p}
        id={id}
        value={value}
        invalid={Boolean(msg || code)}
        describedBy={msg || code ? `${id}-err` : undefined}
        onChange={onChange}
      />
      {p.description ? <span className="wf-run-form__hint">{p.description}</span> : null}
      {msg ? (
        <p id={`${id}-err`} className="wf-run-form__error">
          {msg}
        </p>
      ) : null}
      {code ? (
        <div id={`${id}-err`}>
          <GuidedNotice code={code} />
        </div>
      ) : null}
    </div>
  );
}

/**
 * Run a workflow directly: a typed form over its declared inputs. A control
 * per port type, required ports marked, values checked before anything is
 * sent and sent as their JSON types. A server refusal shows next to its field
 * in plain language. Opens as a non-modal panel (Escape closes).
 */
export default function RunForm({
  workflow,
  returnFocus,
  onStarted,
  onClose,
}: Readonly<{
  workflow: WorkflowDef;
  returnFocus?: HTMLElement | null;
  onStarted: (run: RunDoc) => void;
  onClose: () => void;
}>) {
  const ports = workflow.inputs ?? [];
  const uid = useId();
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(ports.map((p) => [p.name, typeOf(p) === "boolean" ? "false" : initialValue(workflow, p)])),
  );
  const [local, setLocal] = useState<Record<string, string>>({});
  const [server, setServer] = useState<Record<string, string>>({});
  const [topError, setTopError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useRef<HTMLFormElement>(null);

  const change = (name: string, v: string) => {
    setValues((s) => ({ ...s, [name]: v }));
    setLocal(({ [name]: _l, ...rest }) => rest);
    setServer(({ [name]: _s, ...rest }) => rest);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    const { inputs, problems } = collectInputs(ports, values);
    setLocal(problems);
    setServer({});
    setTopError(null);
    const bad = ports.find((p) => problems[p.name]);
    if (bad) {
      form.current?.querySelector<HTMLElement>(`[data-field="${CSS.escape(bad.name)}"] :is(input, textarea)`)?.focus();
      return;
    }
    setBusy(true);
    try {
      onStarted(await runWorkflow(workflow.id, inputs));
    } catch (err) {
      const { codes, top } = refusalOf(err, ports);
      setServer(codes);
      setTopError(top);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel label={`Run ${workflow.name}`} className="wf-panel--wide wf-run-form" returnFocus={returnFocus} onClose={onClose}>
      <h2 className="wf-panel__title">Run {workflow.name}</h2>
      <form ref={form} className="wf-form" noValidate onSubmit={(e) => void submit(e)}>
        {ports.length === 0 ? <p className="wf-ports__empty">This workflow takes no inputs.</p> : null}
        {ports.map((p) => (
          <RunField
            key={p.name}
            port={p}
            id={`${uid}-${p.name}`}
            value={values[p.name] ?? ""}
            message={local[p.name]}
            code={server[p.name]}
            onChange={(v) => change(p.name, v)}
          />
        ))}
        {topError ? <GuidedNotice error={topError} /> : null}
        <div className="wf-form__actions">
          <button type="button" className="wf-button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="wf-run" disabled={busy}>
            Run
          </button>
        </div>
      </form>
    </Panel>
  );
}

function scalarText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function OutputValue({ name, value }: Readonly<{ name: string; value: unknown }>): ReactNode {
  if (typeof value === "object" && value !== null) {
    return (
      <details className="wf-outputs__item" open>
        <summary>{name}</summary>
        <pre>{JSON.stringify(value, null, 2)}</pre>
      </details>
    );
  }
  return (
    <div className="wf-outputs__item">
      <dt>{name}</dt>
      <dd>{scalarText(value)}</dd>
    </div>
  );
}

/** A finished run's exported outputs, in place: scalars as text, objects pretty-printed and collapsible. */
export function RunOutputs({ outputs }: Readonly<{ outputs: Record<string, unknown> }>) {
  const entries = Object.entries(outputs);
  return (
    <section className="wf-outputs" aria-label="Run outputs">
      <h2 className="wf-outputs__title">Outputs</h2>
      {entries.length === 0 ? (
        <p className="wf-ports__empty">This run produced no outputs.</p>
      ) : (
        <dl className="wf-outputs__list">
          {entries.map(([k, v]) => (
            <OutputValue key={k} name={k} value={v} />
          ))}
        </dl>
      )}
    </section>
  );
}
