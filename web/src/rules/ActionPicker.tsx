import { useId, useState } from "react";
import type { Actor, HttpPolicy, RunnerActorParams } from "../api/actors";
import type { Action, Workflow } from "../api/types";

type ParamType = "str" | "int" | "dict" | "any";

interface ParamSpec {
  type: ParamType;
  required?: boolean;
}

interface KindSpec {
  label: string;
  params: Record<string, ParamSpec>;
}

const S: ParamSpec = { type: "str" };
const RS: ParamSpec = { type: "str", required: true };

/**
 * The action catalogue, mirroring culture_rules/model/action_kinds.py: the
 * params each kind takes, their types and which are required. Order is the
 * order the picker lists them in.
 */
export const ACTION_KINDS: Record<string, KindSpec> = {
  noop: { label: "Do nothing", params: {} },
  message: { label: "Send a message", params: { actor: S, channel: RS, text: RS } },
  "github.comment": {
    label: "Comment on GitHub",
    params: { actor: RS, repo: RS, number: { type: "int", required: true }, body: RS },
  },
  "jira.comment": { label: "Comment on Jira", params: { actor: RS, issue: RS, body: RS } },
  "http.call": {
    label: "Call an HTTP endpoint",
    params: { actor: RS, method: RS, url: RS, headers: { type: "dict" }, body: { type: "any" } },
  },
  "machine.command": {
    label: "Run a command on a machine",
    params: { actor: RS, command: RS, args: { type: "dict" } },
  },
};

/** Legacy names the backend accepts as aliases of a catalogued kind. */
const ALIASES: Record<string, string> = { "mesh.message": "message" };
const canonical = (kind: string) => ALIASES[kind] ?? kind;

const REFERENCE = /^(trigger|workflow|vars|rule)\.[A-Za-z0-9_.]+$/;
const CUSTOM = "__custom__";
const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];

/** Common `trigger.data.<field>` names per trigger type prefix; anything else uses a custom path. */
const TRIGGER_FIELDS: { prefix: string; fields: string[] }[] = [
  { prefix: "github.pr", fields: ["number", "repo", "title", "author", "url"] },
  { prefix: "github.", fields: ["repo", "ref", "sha"] },
  { prefix: "jira.", fields: ["issue", "summary"] },
  { prefix: "discord.", fields: ["channel", "text", "author"] },
];

/** A reference to a trigger or workflow value, as a string or a `{"$ref"}` object; else null. */
function refOf(value: unknown): string | null {
  if (typeof value === "string") return REFERENCE.test(value) ? value : null;
  if (value && typeof value === "object" && typeof (value as { $ref?: unknown }).$ref === "string") {
    return (value as { $ref: string }).$ref;
  }
  return null;
}

const isEmpty = (v: unknown) => v === undefined || v === null || (typeof v === "string" && v.trim() === "");
const isDynamic = (v: unknown) => refOf(v) !== null || (typeof v === "string" && v.includes("{{"));

function typeOk(type: ParamType, value: unknown): boolean {
  if (isDynamic(value) || type === "any") return true;
  if (type === "int") return typeof value === "number" && Number.isInteger(value);
  if (type === "str") return typeof value === "string";
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** An action of `kind` with no params filled in; the name is kept across kinds. */
export function blankAction(kind = "noop", name?: string): Action {
  const base: Action = kind === "noop" ? { kind } : { kind, params: {} };
  return name ? { ...base, name } : base;
}

/**
 * What stops an action from being saved, as a guidance code (api/guidance.ts), or null when it
 * is complete. Kinds the editor does not know, and the legacy `mesh.message` placeholder (the
 * backend does not enforce it either), are kept as they are.
 */
export function actionProblem(action: Action): "no_actor_port" | "empty" | "invalid_value" | null {
  const spec = ACTION_KINDS[canonical(action.kind)];
  if (!spec || action.kind === "mesh.message") return null;
  const params = (action.params ?? {}) as Record<string, unknown>;
  if (spec.params.actor?.required && isEmpty(params.actor)) return "no_actor_port";
  for (const [name, p] of Object.entries(spec.params)) {
    if (name !== "actor" && p.required && isEmpty(params[name])) return "empty";
  }
  for (const [name, p] of Object.entries(spec.params)) {
    if (!isEmpty(params[name]) && !typeOk(p.type, params[name])) return "invalid_value";
  }
  return null;
}

const enabled = (a: Actor) => a.enabled !== false;
const declares = (a: Actor, kind: string) =>
  a.kind === "app" && enabled(a) && (a.params?.actions ?? []).includes(kind);
const allowsHttp = (a: Actor) => enabled(a) && ((a.params?.http as HttpPolicy | undefined)?.allow ?? []).length > 0;
const commandsOf = (a?: Actor) => (a?.params as RunnerActorParams | undefined)?.commands ?? {};

/** Enabled actors able to perform `kind`: app actors by declared actions, runners by command. */
export function actorsFor(kind: string, actors: Actor[]): Actor[] {
  switch (canonical(kind)) {
    case "machine.command":
      return actors.filter((a) => a.kind === "runner" && enabled(a));
    case "http.call":
      return actors.filter((a) => declares(a, "http.call") || allowsHttp(a));
    case "noop":
      return [];
    default:
      return actors.filter((a) => declares(a, canonical(kind)));
  }
}

/** Mesh message and noop need no actor; every other kind needs one enabled actor that supports it. */
const offered = (kind: string, actors: Actor[]) =>
  kind === "noop" || kind === "message" || actorsFor(kind, actors).length > 0;

function triggerFields(triggerType: string | undefined): string[] {
  const hit = TRIGGER_FIELDS.find((t) => triggerType?.startsWith(t.prefix));
  return hit ? hit.fields : [];
}

interface MappingRefs {
  trigger: string[];
  workflow: string[];
}

interface FieldProps {
  /** Visible name of the param; also what the map select and chip are named after. */
  label: string;
  type: ParamType;
  value: unknown;
  refs: MappingRefs;
  required?: boolean;
  testKey: string;
  multiline?: boolean;
  onChange: (value: unknown) => void;
}

/**
 * One param as a literal or a mapping chip. A mapped value is saved as its reference string
 * (`trigger.data.number`) and shown as a chip with that text; the text is never required, since
 * the map select offers the trigger's fields and the workflow's outputs.
 */
function ValueField(props: Readonly<FieldProps>) {
  const { label, value, testKey, onChange } = props;
  const id = useId();
  const [custom, setCustom] = useState(false);
  const [path, setPath] = useState(refOf(value) ?? "");
  const ref = refOf(value);

  if (ref && !custom) {
    return (
      <fieldset className="action-picker__field plain-group" aria-labelledby={`${id}-name`}>
        <span id={`${id}-name`}>{label}</span>
        <div className="action-picker__mapped">
          <span className="mapping-chip" data-testid={`chip-${testKey}`}>
            {ref}
          </span>
          <button
            type="button"
            className="btn action-picker__unmap"
            aria-label={`Use a fixed value for ${label}`}
            onClick={() => {
              setCustom(false);
              onChange(undefined);
            }}
          >
            Fixed value
          </button>
        </div>
      </fieldset>
    );
  }

  return (
    <div className="action-picker__field">
      {custom ? (
        <PathEditor
          id={id}
          label={label}
          path={path}
          onPath={(next) => {
            setPath(next);
            onChange(REFERENCE.test(next) ? next : undefined);
          }}
          onDone={() => setCustom(false)}
        />
      ) : (
        <>
          <LiteralInput id={id} {...props} />
          <MapSelect
            id={id}
            label={label}
            refs={props.refs}
            onPick={(picked) => {
              if (picked === CUSTOM) {
                setPath("");
                setCustom(true);
              } else if (picked) onChange(picked);
            }}
          />
        </>
      )}
    </div>
  );
}

function PathEditor({
  id,
  label,
  path,
  onPath,
  onDone,
}: Readonly<{ id: string; label: string; path: string; onPath: (next: string) => void; onDone: () => void }>) {
  return (
    <>
      <label>
        <span id={`${id}-path`}>Path for {label}</span>
        <input
          aria-labelledby={`${id}-path`}
          value={path}
          placeholder="trigger.data.number"
          spellCheck={false}
          onChange={(e) => onPath(e.target.value)}
        />
      </label>
      <p className="trigger-picker__hint">
        Start with trigger., workflow.outputs. or vars., for example trigger.data.number.
      </p>
      <button type="button" className="btn" onClick={onDone}>
        Done
      </button>
    </>
  );
}

/** The fixed-value input for a param: read-only for a stored object, a textarea for long text. */
function LiteralInput({
  id,
  label,
  type,
  value,
  required,
  multiline,
  onChange,
}: Readonly<FieldProps & { id: string }>) {
  const text = typeof value === "string" || typeof value === "number" ? String(value) : "";
  const literal = (v: string): unknown => {
    if (v === "") return undefined;
    return type === "int" && /^-?\d+$/.test(v) ? Number(v) : v;
  };
  let control;
  if (value !== undefined && value !== null && typeof value === "object") {
    control = <input aria-labelledby={`${id}-name`} value={JSON.stringify(value)} readOnly />;
  } else if (multiline) {
    control = <textarea aria-labelledby={`${id}-name`} value={text} onChange={(e) => onChange(literal(e.target.value))} />;
  } else {
    control = (
      <input
        aria-labelledby={`${id}-name`}
        value={text}
        inputMode={type === "int" ? "numeric" : undefined}
        onChange={(e) => onChange(literal(e.target.value))}
      />
    );
  }
  return (
    <label>
      <span>
        <span id={`${id}-name`}>{label}</span>
        {required ? (
          <span className="action-picker__required" aria-hidden="true">
            {" "}
            *
          </span>
        ) : null}
      </span>
      {control}
    </label>
  );
}

/** The "Use a value from…" select: the trigger's fields, the workflow's outputs, or a custom path. */
function MapSelect({
  id,
  label,
  refs,
  onPick,
}: Readonly<{ id: string; label: string; refs: MappingRefs; onPick: (picked: string) => void }>) {
  return (
    <label className="action-picker__map">
      <span id={`${id}-map`}>Map {label}</span>
      <select aria-labelledby={`${id}-map`} value="" onChange={(e) => onPick(e.target.value)}>
        <option value="">Use a value from…</option>
        {refs.trigger.length > 0 ? (
          <optgroup label="The trigger">
            {refs.trigger.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </optgroup>
        ) : null}
        {refs.workflow.length > 0 ? (
          <optgroup label="The workflow's outputs">
            {refs.workflow.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </optgroup>
        ) : null}
        <option value={CUSTOM}>Custom path…</option>
      </select>
    </label>
  );
}

interface HeadersProps {
  value: unknown;
  onChange: (value: Record<string, string> | undefined) => void;
}

interface HeaderRow {
  id: number;
  name: string;
  value: string;
}

let headerRowId = 0;
const headerRow = (name: string, value: string): HeaderRow => ({ id: ++headerRowId, name, value });

/** Free key/value rows for a dict param (http.call headers). */
function HeadersField({ value, onChange }: Readonly<HeadersProps>) {
  const id = useId();
  const [rows, setRows] = useState<HeaderRow[]>(() =>
    Object.entries((value ?? {}) as Record<string, unknown>).map(([k, v]) => headerRow(k, String(v))),
  );
  const commit = (next: HeaderRow[]) => {
    setRows(next);
    const entries = next.filter((r) => r.name.trim());
    onChange(entries.length ? Object.fromEntries(entries.map((r) => [r.name.trim(), r.value])) : undefined);
  };
  const patch = (row: HeaderRow, change: Partial<HeaderRow>) =>
    commit(rows.map((r) => (r === row ? { ...r, ...change } : r)));
  return (
    <fieldset className="action-picker__field plain-group" aria-labelledby={`${id}-h`}>
      <span id={`${id}-h`}>Headers</span>
      {rows.map((row, i) => (
        <div className="action-picker__pair" key={row.id}>
          <input
            aria-label={`Header name ${i + 1}`}
            value={row.name}
            onChange={(e) => patch(row, { name: e.target.value })}
          />
          <input
            aria-label={`Header value ${i + 1}`}
            value={row.value}
            onChange={(e) => patch(row, { value: e.target.value })}
          />
        </div>
      ))}
      <button type="button" className="btn" onClick={() => setRows([...rows, headerRow("", "")])}>
        Add header
      </button>
    </fieldset>
  );
}

interface Props {
  value: Action;
  actors: Actor[];
  /** The rule's trigger event type, for the common trigger fields offered as mappings. */
  triggerType?: string;
  /** The rule's workflow, whose outputs can be mapped. */
  workflow?: Workflow;
  onChange: (action: Action) => void;
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

interface ParamFieldsProps {
  id: string;
  spec: KindSpec;
  params: Record<string, unknown>;
  actor: Actor | undefined;
  refs: MappingRefs;
  setParam: (key: string, v: unknown) => void;
  setParams: (next: Record<string, unknown>) => void;
}

/** One typed field per param of the chosen action kind (the actor select is rendered separately). */
function ParamFields(props: Readonly<ParamFieldsProps>) {
  return (
    <>
      {Object.entries(props.spec.params)
        .filter(([name]) => name !== "actor")
        .map(([name, p]) => (
          <ParamField key={name} name={name} p={p} {...props} />
        ))}
    </>
  );
}

/** A single param's field: the command / args / method selects, the headers rows, or a value. */
function ParamField({
  id,
  name,
  p,
  params,
  actor,
  refs,
  setParam,
  setParams,
}: Readonly<ParamFieldsProps & { name: string; p: ParamSpec }>) {
  const command = typeof params.command === "string" ? params.command : "";
  const commands = commandsOf(actor);
  const declared = Object.keys(commands[command]?.params ?? {});
  const args = (params.args ?? {}) as Record<string, unknown>;
  if (name === "command") {
    return (
      <label key={name}>
        <span id={`${id}-command`}>Command</span>
        <select
          aria-labelledby={`${id}-command`}
          value={command}
          disabled={!actor}
          onChange={(e) => setParams({ ...params, command: e.target.value || undefined, args: undefined })}
        >
          <option value="">Choose a command…</option>
          {command && !(command in commands) ? <option value={command}>{command} (not registered)</option> : null}
          {Object.keys(commands).map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </label>
    );
  }
  if (name === "args") {
    return declared.map((arg) => (
      <ValueField
        key={`${command}-${arg}`}
        label={arg}
        type="str"
        testKey={arg}
        refs={refs}
        value={args[arg]}
        onChange={(v) => {
          const next = Object.fromEntries(Object.entries({ ...args, [arg]: v }).filter(([, x]) => x !== undefined));
          setParam("args", Object.keys(next).length ? next : undefined);
        }}
      />
    ));
  }
  if (name === "method") {
    return (
      <label key={name}>
        <span id={`${id}-method`}>Method</span>
        <select
          aria-labelledby={`${id}-method`}
          value={typeof params.method === "string" ? params.method : ""}
          onChange={(e) => setParam("method", e.target.value || undefined)}
        >
          <option value="">Choose a method…</option>
          {METHODS.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </label>
    );
  }
  if (p.type === "dict") {
    return <HeadersField key={name} value={params[name]} onChange={(v) => setParam(name, v)} />;
  }
  return (
    <ValueField
      key={name}
      label={cap(name)}
      type={p.type}
      testKey={name}
      refs={refs}
      required={p.required}
      multiline={name === "body" || name === "text"}
      value={params[name]}
      onChange={(v) => setParam(name, v)}
    />
  );
}

/**
 * "Then what happens?" as typed choices: a kind offered only if some enabled actor can do it, the
 * actor limited to those that can, then one typed field per param. Any param can be mapped from
 * the trigger or the workflow's outputs instead of typed. Controlled: `value` is the action that
 * would be saved.
 */
export default function ActionPicker({ value, actors, triggerType, workflow, onChange }: Readonly<Props>) {
  const id = useId();
  const kind = canonical(value.kind);
  const spec = ACTION_KINDS[kind];
  const params = (value.params ?? {}) as Record<string, unknown>;
  const eligible = actorsFor(kind, actors);
  const actorId = typeof params.actor === "string" ? params.actor : "";
  const actor = actors.find((a) => a.id === actorId);
  const refs: MappingRefs = {
    trigger: triggerFields(triggerType).map((f) => `trigger.data.${f}`),
    workflow: (workflow?.outputs ?? []).map((o) => `workflow.outputs.${o.name}`),
  };

  const setParams = (next: Record<string, unknown>) => {
    const clean = Object.fromEntries(Object.entries(next).filter(([, v]) => v !== undefined));
    onChange({ ...value, params: clean });
  };
  const setParam = (key: string, v: unknown) => setParams({ ...params, [key]: v });
  const chooseKind = (next: string) => {
    if (next !== kind) onChange(blankAction(next, value.name));
  };

  const kindOptions = Object.entries(ACTION_KINDS).filter(([k]) => offered(k, actors) || k === kind);
  const known = spec !== undefined;
  const extras = Object.entries(params).filter(([k]) => spec && !(k in spec.params));

  return (
    <fieldset className="trigger-picker action-picker">
      <legend>Then what happens?</legend>
      <label>
        <span id={`${id}-kind`}>What happens</span>
        <select aria-labelledby={`${id}-kind`} value={known ? kind : value.kind} onChange={(e) => chooseKind(e.target.value)}>
          {known ? null : <option value={value.kind}>{value.kind}</option>}
          {kindOptions.map(([k, s]) => (
            <option key={k} value={k}>
              {s.label}
            </option>
          ))}
        </select>
      </label>
      {known ? null : (
        <p className="trigger-picker__words">
          This rule uses a “{value.kind}” action the editor cannot change; it is kept as it is.
        </p>
      )}
      {known && kind !== "noop" && eligible.length === 0 && spec.params.actor?.required ? (
        <output className="trigger-picker__empty">
          No enabled actor can do this yet. Add one on the Actors tab.
        </output>
      ) : null}

      {known && spec.params.actor ? (
        <label>
          <span id={`${id}-actor`}>Actor</span>
          <select
            aria-labelledby={`${id}-actor`}
            value={actorId}
            onChange={(e) => setParams({ ...params, actor: e.target.value || undefined, command: undefined, args: undefined })}
          >
            <option value="">{kind === "message" ? "The Culture mesh" : "Choose an actor…"}</option>
            {actorId && !eligible.some((a) => a.id === actorId) ? (
              <option value={actorId}>{actorId} (unavailable)</option>
            ) : null}
            {eligible.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {known ? (
        <ParamFields id={id} spec={spec} params={params} actor={actor} refs={refs} setParam={setParam} setParams={setParams} />
      ) : null}

      {extras.map(([k, v]) => (
        <fieldset key={k} className="action-picker__field plain-group" aria-label={k}>
          <span>{k}</span>
          {refOf(v) ? (
            <span className="mapping-chip" data-testid={`chip-${k}`}>
              {refOf(v)}
            </span>
          ) : (
            <span className="trigger-picker__words">{typeof v === "string" ? v : JSON.stringify(v)}</span>
          )}
        </fieldset>
      ))}

      <label>
        <span id={`${id}-name`}>Action label</span>
        <input
          aria-labelledby={`${id}-name`}
          value={value.name ?? ""}
          placeholder="Optional"
          onChange={(e) => onChange({ ...value, name: e.target.value })}
        />
      </label>
    </fieldset>
  );
}
