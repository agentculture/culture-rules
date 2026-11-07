import { useEffect, useId, useState, type ReactNode } from "react";
import { getDiscordTargets } from "../api/actors";
import { useDiscordTargets, type LoadTargets } from "./useDiscordTargets";
import type { Actor, RunnerActorParams } from "../api/actors";
import type { Action, DiscordButton, Workflow } from "../api/types";
import {
  kindOf,
  useActionCatalogue,
  type ActionCatalogue,
  type ActionKindSpec,
  type ActionParam,
  type ActorClause,
} from "../api/actions";

type ParamType = "str" | "int" | "number" | "bool" | "dict" | "list" | "any" | "json";

interface Choice {
  value: string;
  label: string;
}

/** A served param as the fields below read it: a widget can turn the type into `json` or `number`. */
const typeOf = (p: ActionParam): ParamType => {
  if (p.widget === "json" || p.widget === "number") return p.widget;
  return p.type as ParamType;
};
const isRequired = (p: ActionParam) => p.required || p.needed;
const choicesOf = (p: ActionParam): readonly Choice[] | undefined => (p.choices.length ? p.choices : undefined);

const REFERENCE = /^(trigger|workflow|vars|rule)\.[A-Za-z0-9_.]+$/;
const CUSTOM = "__custom__";

/** Common `trigger.data.<field>` names per trigger type prefix; anything else uses a custom path. */
const TRIGGER_FIELDS: { prefix: string; fields: string[] }[] = [
  { prefix: "github.", fields: ["number", "repository", "title", "author", "url"] },
  { prefix: "jira.", fields: ["key", "project", "summary", "status", "url"] },
  { prefix: "discord.", fields: ["channel_id", "guild_id", "content", "author_name", "message_id"] },
];

/**
 * How the picker shows a stored action: a kind whose catalogue entry carries `shown_as` is a
 * legacy form another kind took over (a `message` naming an actor was a Discord message before
 * `discord.message` existed), so when it sets `when_param` it is shown (and saved) as that kind.
 */
export function viewOf(action: Action, catalogue?: ActionCatalogue): Action {
  const shownAs = kindOf(catalogue, action.kind)?.shown_as;
  const value = shownAs ? (action.params as Record<string, unknown> | undefined)?.[shownAs.when_param] : undefined;
  return shownAs && typeof value === "string" && value ? { ...action, kind: shownAs.kind } : action;
}

/** A reference to a trigger or workflow value, as a string or a `{"$ref"}` object; else null. */
function refOf(value: unknown): string | null {
  if (typeof value === "string") return REFERENCE.test(value) ? value : null;
  if (value && typeof value === "object" && typeof (value as { $ref?: unknown }).$ref === "string") {
    return (value as { $ref: string }).$ref;
  }
  return null;
}

const isEmpty = (v: unknown) =>
  v === undefined || v === null || (typeof v === "string" && v.trim() === "") || (Array.isArray(v) && v.length === 0);
const isDynamic = (v: unknown) => refOf(v) !== null || (typeof v === "string" && v.includes("{{"));

function typeOk(p: ActionParam, value: unknown): boolean {
  const type = typeOf(p);
  const options = choicesOf(p);
  if (isDynamic(value) || type === "any") return true;
  if (options) return options.some((o) => o.value === value);
  if (type === "int") return typeof value === "number" && Number.isInteger(value);
  if (type === "number") return typeof value === "number" && Number.isFinite(value);
  if (type === "str") return typeof value === "string";
  if (type === "bool") return typeof value === "boolean";
  if (type === "list") return Array.isArray(value);
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * An action of `kind` (default: the first kind in catalogue order) with no params filled in, and
 * no `params` at all when the kind takes none; the name is kept across kinds.
 */
export function blankAction(catalogue: ActionCatalogue, kind?: string, name?: string): Action {
  const spec = kind === undefined ? catalogue.items[0] : kindOf(catalogue, kind);
  const chosen = kind ?? spec?.kind ?? "";
  const base: Action = spec && spec.params.length === 0 ? { kind: chosen } : { kind: chosen, params: {} };
  return name ? { ...base, name } : base;
}

/**
 * What stops an action from being saved, as a guidance code (api/guidance.ts), or null when it
 * is complete. Kinds the catalogue does not hold (or while it is not loaded), and a legacy alias
 * of a lenient kind (the `mesh.message` placeholder; the backend does not enforce it either), are
 * kept as they are.
 */
export function actionProblem(
  stored: Action,
  catalogue?: ActionCatalogue,
): "no_actor_port" | "empty" | "invalid_value" | null {
  const action = viewOf(stored, catalogue);
  const spec = kindOf(catalogue, action.kind);
  if (!spec || (spec.lenient && spec.kind !== action.kind)) return null;
  const params = (action.params ?? {}) as Record<string, unknown>;
  if (paramNamed(spec, "actor")?.required && isEmpty(params.actor)) return "no_actor_port";
  for (const p of spec.params) {
    if (p.name !== "actor" && isRequired(p) && isEmpty(params[p.name])) return "empty";
  }
  if (spec.any_of.some((group) => group.every((name) => isEmpty(params[name])))) return "empty";
  for (const p of spec.params) {
    if (!isEmpty(params[p.name]) && !typeOk(p, params[p.name])) return "invalid_value";
  }
  return buttonsInvalid(spec, params) ? "invalid_value" : null;
}

const paramNamed = (spec: ActionKindSpec | undefined, name: string) => spec?.params.find((p) => p.name === name);

/** A discord.message button whose posted id would pass Discord's 100 characters, or whose
 * one-time code has no value to be bound to. */
function buttonsInvalid(spec: ActionKindSpec, params: Record<string, unknown>): boolean {
  const field = spec.params.find((p) => p.widget === "buttons");
  const rows = field ? params[field.name] : undefined;
  if (!Array.isArray(rows)) return false;
  return (rows as DiscordButton[]).some((b) => buttonIdTooLong(b) || codeWithoutValue(b));
}

const enabled = (a: Actor) => a.enabled !== false;
const commandsOf = (a?: Actor) => (a?.params as RunnerActorParams | undefined)?.commands ?? {};

/** A param of an actor holds something: a non-empty string or list (a dotted path reads nested objects). */
function actorHas(a: Actor, path: string): boolean {
  let at: unknown = a.params;
  for (const key of path.split(".")) {
    at = isJsonObject(at) ? at[key] : undefined;
  }
  return typeof at === "string" ? at !== "" : Array.isArray(at) && at.length > 0;
}

function matches(a: Actor, kind: string, c: ActorClause): boolean {
  if (c.kinds && !c.kinds.includes(a.kind)) return false;
  if (c.not_kinds?.includes(a.kind)) return false;
  if (c.surface && a.params?.surface !== c.surface) return false;
  if (c.declares_action && !(a.kind === "app" && (a.params?.actions ?? []).includes(kind))) return false;
  if (c.has && !c.has.every((path) => actorHas(a, path))) return false;
  return !c.has_any || c.has_any.some((path) => actorHas(a, path));
}

/**
 * Enabled actors able to perform `kind`: those matching any of the catalogue's actor clauses for
 * it (`actors` in the served kind: actor kinds, a surface, a declared action, params that are
 * set). A kind the catalogue does not hold has none.
 */
export function actorsFor(kind: string, actors: Actor[], catalogue?: ActionCatalogue): Actor[] {
  const spec = kindOf(catalogue, kind);
  if (!spec || !paramNamed(spec, "actor")) return [];
  return actors.filter((a) => enabled(a) && spec.actors.some((c) => matches(a, spec.kind, c)));
}

/** Kinds whose server and channel are picked from what the bot can see (a `discord_channel` param). */
const hasTargetPicker = (spec: ActionKindSpec | undefined) => !!spec?.params.some((p) => p.widget === "discord_channel");

/** A kind that needs no actor is always offered; every other needs one enabled actor that supports it. */
const offered = (spec: ActionKindSpec, actors: Actor[], catalogue: ActionCatalogue) =>
  spec.actorless || actorsFor(spec.kind, actors, catalogue).length > 0;

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

const INTEGER = /^-?\d+$/;
const DECIMAL = /^-?\d+(\.\d+)?$/;

/** A typed literal from the text in a field: numbers once they parse, else the text as is. */
function literalOf(type: ParamType, text: string): unknown {
  if (text === "") return undefined;
  if (type === "int" && INTEGER.test(text)) return Number(text);
  if (type === "number" && DECIMAL.test(text)) return Number(text);
  return text;
}

const splitList = (text: string) =>
  text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

/** A list param (email.send `to`) as comma-separated text; saved as an array of the entries. */
function ListInput({ id, value, onChange }: Readonly<{ id: string; value: unknown; onChange: (v: unknown) => void }>) {
  const [text, setText] = useState(() => (Array.isArray(value) ? value.join(", ") : ""));
  return (
    <input
      aria-labelledby={`${id}-name`}
      aria-describedby={`${id}-list-hint`}
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        const items = splitList(e.target.value);
        onChange(items.length ? items : undefined);
      }}
    />
  );
}

/** An editable JSON object. Invalid drafts stay in state so save validation refuses them. */
function JsonObjectInput({ id, value, onChange }: Readonly<{ id: string; value: unknown; onChange: (v: unknown) => void }>) {
  let display = "";
  if (typeof value === "string") display = value;
  else if (value !== undefined) display = JSON.stringify(value);
  const [text, setText] = useState(display);
  useEffect(() => {
    setText(display);
  }, [display]);
  return <textarea
    aria-labelledby={`${id}-name`}
    aria-invalid={value !== undefined && !isJsonObject(value)}
    value={text}
    rows={4}
    onChange={(e) => {
      const next = e.target.value;
      setText(next);
      if (!next.trim()) { onChange(undefined); return; }
      try {
        const parsed: unknown = JSON.parse(next);
        onChange(isJsonObject(parsed) ? parsed : next);
      } catch {
        onChange(next);
      }
    }}
  />;
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
  const literal = (v: string) => literalOf(type, v);
  const numeric = type === "int" || type === "number";
  let control;
  if (type === "json") {
    control = <JsonObjectInput id={id} value={value} onChange={onChange} />;
  } else if (type === "list") {
    control = <ListInput id={id} value={value} onChange={onChange} />;
  } else if (value !== undefined && value !== null && typeof value === "object") {
    control = <input aria-labelledby={`${id}-name`} value={JSON.stringify(value)} readOnly />;
  } else if (multiline) {
    control = <textarea aria-labelledby={`${id}-name`} value={text} onChange={(e) => onChange(literal(e.target.value))} />;
  } else {
    control = (
      <input
        aria-labelledby={`${id}-name`}
        value={text}
        inputMode={numeric ? "decimal" : undefined}
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
      {type === "list" ? (
        <span id={`${id}-list-hint`} className="trigger-picker__hint">
          Separate with commas.
        </span>
      ) : null}
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

interface DiscordTargetProps {
  id: string;
  actorId: string;
  params: Record<string, unknown>;
  refs: MappingRefs;
  load: LoadTargets;
  setParams: (next: Record<string, unknown>) => void;
}

/**
 * The Discord server and channel for a `discord.message`, picked from what the actor's bot can
 * see (`GET /actors/{id}/discord/targets`). A private channel the bot was not added to is listed
 * but marked. The channel can instead be mapped from the trigger (reply where a message came
 * from); when the bot's channels cannot be loaded, the channel id is typed.
 */
function DiscordTarget({ id, actorId, params, refs, load, setParams }: Readonly<DiscordTargetProps>) {
  const { targets, failed } = useDiscordTargets(actorId, load, (t) => {
    if (t.guilds.length === 1 && !params.guild) setParams({ ...params, guild: t.guilds[0].id });
  });

  const channelValue = (v: unknown) => (
    <ValueField
      label="Channel"
      type="str"
      testKey="channel"
      refs={refs}
      required
      value={v}
      onChange={(next) => setParams({ ...params, channel: next })}
    />
  );
  if (!actorId) return null;
  if (failed) {
    return (
      <>
        <output className="trigger-picker__empty">
          Could not load the bot&apos;s servers and channels; type the channel id instead.
        </output>
        {channelValue(params.channel)}
      </>
    );
  }
  if (!targets) return <output className="trigger-picker__words" aria-live="polite">Loading the bot&apos;s servers…</output>;

  const guildId = typeof params.guild === "string" ? params.guild : "";
  const guild = targets.guilds.find((g) => g.id === guildId);
  const channelId = typeof params.channel === "string" ? params.channel : "";
  const mapped = refOf(params.channel) !== null;
  return (
    <>
      <label>
        <span id={`${id}-guild`}>Server</span>
        <select
          aria-labelledby={`${id}-guild`}
          value={guildId}
          onChange={(e) => {
            const next = targets.guilds.find((g) => g.id === e.target.value);
            const keep = mapped || next?.channels.some((c) => c.id === channelId);
            setParams({ ...params, guild: e.target.value || undefined, channel: keep ? params.channel : undefined });
          }}
        >
          <option value="">Choose a server…</option>
          {guildId && !guild ? <option value={guildId}>{guildId} (the bot is not in it)</option> : null}
          {targets.guilds.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
      </label>
      {mapped ? (
        channelValue(params.channel)
      ) : (
        <div className="action-picker__field">
          <label>
            <span id={`${id}-channel`}>Channel</span>
            <select
              aria-labelledby={`${id}-channel`}
              value={channelId}
              disabled={!guild}
              onChange={(e) => setParams({ ...params, channel: e.target.value || undefined })}
            >
              <option value="">Choose a channel…</option>
              {channelId && !guild?.channels.some((c) => c.id === channelId) ? (
                <option value={channelId}>{channelId}</option>
              ) : null}
              {(guild?.channels ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.visible ? `#${c.name}` : `#${c.name} (the bot was not added)`}
                </option>
              ))}
            </select>
          </label>
          <MapSelect
            id={`${id}-channel`}
            label="Channel"
            refs={refs}
            onPick={(picked) => {
              if (picked && picked !== CUSTOM) setParams({ ...params, channel: picked });
            }}
          />
        </div>
      )}
    </>
  );
}

interface Props {
  /** The stored action; absent: a blank one of the first kind in the catalogue. */
  value?: Action;
  actors: Actor[];
  /** The rule's trigger event type, for the common trigger fields offered as mappings. */
  triggerType?: string;
  /** The rule's workflow, whose outputs can be mapped. */
  workflow?: Workflow;
  onChange: (action: Action) => void;
  /** Where a Discord message's servers and channels come from (default: the API). */
  loadDiscordTargets?: LoadTargets;
  /** The action catalogue to build from (default: the API's, `GET /actions/kinds`, loaded once). */
  catalogue?: ActionCatalogue;
}

/** What a picker calls a param: its served label, else its name in words. */
const labelOf = (p: ActionParam) => p.label || cap(p.name);

const cap = (s: string) => {
  const words = s.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
};

interface ParamFieldsProps {
  id: string;
  load: LoadTargets;
  spec: ActionKindSpec;
  params: Record<string, unknown>;
  actor: Actor | undefined;
  refs: MappingRefs;
  setParam: (key: string, v: unknown) => void;
  setParams: (next: Record<string, unknown>) => void;
}

/**
 * The rarely-set params behind a disclosure, collapsed unless one already has a value (so nothing
 * stored is hidden silently); a native `details`, so the keyboard opens it.
 */
function MoreSettings({ open, children }: Readonly<{ open: boolean; children: ReactNode }>) {
  const [opened, setOpened] = useState(false);
  return (
    <details className="action-picker__more" open={open || opened} onToggle={(e) => setOpened(e.currentTarget.open)}>
      <summary>More settings</summary>
      {children}
    </details>
  );
}

/** One typed field per param of the chosen action kind (the actor select is rendered separately). */
function ParamFields(props: Readonly<ParamFieldsProps>) {
  const discord = hasTargetPicker(props.spec);
  const actorId = typeof props.params.actor === "string" ? props.params.actor : "";
  const shown = props.spec.params
    .filter((p) => p.name !== "actor" && p.widget !== "hidden")
    .filter((p) => !(discord && (p.widget === "discord_guild" || p.widget === "discord_channel")))
    .filter((p) => !hiddenForActor(p, props.actor));
  const advanced = shown.filter((p) => p.advanced);
  return (
    <>
      {discord ? (
        <DiscordTarget
          id={props.id}
          actorId={actorId}
          params={props.params}
          refs={props.refs}
          load={props.load}
          setParams={props.setParams}
        />
      ) : null}
      {shown.filter((p) => !p.advanced).map((p) => (
        <ParamField key={p.name} p={p} {...props} />
      ))}
      {advanced.length > 0 ? (
        <MoreSettings open={advanced.some((p) => !isEmpty(props.params[p.name]))}>
          {advanced.map((p) => (
            <ParamField key={p.name} p={p} {...props} />
          ))}
        </MoreSettings>
      ) : null}
    </>
  );
}

interface ChoiceFieldProps {
  id: string;
  p: ActionParam;
  options: readonly Choice[];
  value: unknown;
  onPick: (value: string | undefined) => void;
}

/** Params that belong to the chosen actor (its command and arguments, the Discord server) and go with it. */
const clearedWithActor = (spec: ActionKindSpec): Record<string, undefined> =>
  Object.fromEntries(
    spec.params
      .filter((p) => ["command", "command_args", "discord_guild"].includes(p.widget))
      .map((p) => [p.name, undefined]),
  );

/** A param the chosen actor makes moot: it is shown only when the actor has `needs_actor_param` set. */
function hiddenForActor(p: ActionParam, actor: Actor | undefined): boolean {
  return !!p.needs_actor_param && !!actor && !actorHas(actor, p.needs_actor_param);
}

/**
 * On an actor change, clear a value the new actor refuses: one that is not among the values the
 * actor lists for its param (`options_from`: a model brief, a library kind) and one the actor
 * has no setting for (`needs_actor_param`: a device id without a queue). Hidden values would
 * otherwise stay saved where the step can no longer show or fix them (#91 review).
 */
function actorCarryOver(
  spec: ActionKindSpec,
  params: Record<string, unknown>,
  actor: Actor | undefined,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const p of spec.params) {
    if (p.options_from) {
      out[p.name] = actorChoices(actor, p.options_from).some((cc) => cc.value === params[p.name]) ? params[p.name] : undefined;
    } else if (p.needs_actor_param) {
      out[p.name] = actor && actorHas(actor, p.needs_actor_param) ? params[p.name] : undefined;
    }
  }
  return out;
}

/**
 * The names an actor's `params[key]` holds as choices: a string list (library.push `kinds`) or
 * the keys of a mapping (llm.complete `briefs`, name to text; the text is never read here).
 */
function actorChoices(actor: Actor | undefined, key: string): Choice[] {
  const list = actor?.params?.[key];
  if (isJsonObject(list)) return Object.keys(list).map((v) => ({ value: v, label: cap(v) }));
  if (!Array.isArray(list)) return [];
  return list.filter((v): v is string => typeof v === "string").map((v) => ({ value: v, label: cap(v) }));
}

/** A param with a closed set of values (http.call `method`, search.web `freshness`) as a select. */
function ChoiceField({ id, p, options, value, onPick }: Readonly<ChoiceFieldProps>) {
  const current = typeof value === "string" ? value : "";
  return (
    <label>
      <span id={id}>{labelOf(p)}</span>
      <select aria-labelledby={id} value={current} onChange={(e) => onPick(e.target.value || undefined)}>
        <option value="">{p.choose || "Choose…"}</option>
        {current && !options.some((o) => o.value === current) ? <option value={current}>{current}</option> : null}
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

const BUTTON_STYLES = ["success", "primary", "secondary", "danger"] as const;
const MAX_BUTTONS = 5;
type ButtonSpec = DiscordButton;

/** The server appends `:<22-char token>` to a valued button's id, so it may be 77 long at most. */
const MAX_CUSTOM_ID = 100;
const MAX_VALUED_CUSTOM_ID = 77;
const hasValue = (b: ButtonSpec) => b.value !== undefined && b.value !== null;
const idLimit = (b: ButtonSpec) => (hasValue(b) ? MAX_VALUED_CUSTOM_ID : MAX_CUSTOM_ID);
/** A one-time code is bound to the button's value, so it needs one. */
function codeWithoutValue(b: ButtonSpec): boolean {
  return b.code === true && b.value === undefined;
}

function buttonIdTooLong(b: ButtonSpec): boolean {
  return (b.custom_id ?? "").length > idLimit(b);
}

function isJsonObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * A button's optional `value`: JSON text, saved only when it parses to an object (empty text
 * removes it). The bot's own post keeps it server-side; a clicker cannot change it.
 */
function ButtonValueField({
  index,
  value,
  onChange,
}: Readonly<{ index: number; value: unknown; onChange: (v: Record<string, unknown> | undefined) => void }>) {
  const [text, setText] = useState(() => (value === undefined ? "" : JSON.stringify(value)));
  const [problem, setProblem] = useState(false);
  const id = `button-${index}-value-problem`;
  // Follow the prop when it changes to something the text does not already stand for (a row
  // removed above, params reset from outside); an invalid in-progress edit leaves the prop alone.
  const key = JSON.stringify(value) ?? "";
  useEffect(() => {
    let parsed: unknown;
    try {
      parsed = text.trim() ? JSON.parse(text) : undefined;
    } catch {
      parsed = undefined;
    }
    if (JSON.stringify(parsed) !== (key || undefined)) {
      setText(key);
      setProblem(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const change = (next: string) => {
    setText(next);
    if (!next.trim()) {
      setProblem(false);
      onChange(undefined);
      return;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(next);
    } catch {
      parsed = undefined;
    }
    if (isJsonObject(parsed)) {
      setProblem(false);
      onChange(parsed);
    } else {
      setProblem(true);
    }
  };
  return (
    <label>
      <span>{`Button ${index + 1} value`}</span>
      <textarea
        value={text}
        rows={2}
        aria-invalid={problem}
        aria-describedby={problem ? id : undefined}
        placeholder='{"number": 12}'
        onChange={(e) => change(e.target.value)}
      />
      {problem ? (
        <span id={id} className="notice notice--error" role="alert">
          {`Button ${index + 1} value must be a JSON object`}
        </span>
      ) : null}
    </label>
  );
}

/**
 * discord.message `buttons`: up to five rows of label, style, custom id and an optional value,
 * with add and remove.
 */
function ButtonsField({ value, onChange }: Readonly<{ value: unknown; onChange: (v: unknown) => void }>) {
  const rows: ButtonSpec[] = Array.isArray(value) ? (value as ButtonSpec[]) : [];
  const save = (next: ButtonSpec[]) => onChange(next.length ? next : undefined);
  const edit = (i: number, patch: Partial<ButtonSpec>) =>
    save(
      rows.map((r, j) => {
        if (j !== i) return r;
        const next = { ...r, ...patch };
        // clearing the value keeps the code: the save is blocked until a value is back or the
        // code is turned off, so retyping a value never drops it silently
        if ("value" in patch && patch.value === undefined) delete next.value;
        if ("code" in patch && patch.code === undefined) delete next.code;
        return next;
      }),
    );
  return (
    <fieldset className="action-picker__buttons">
      <legend>Buttons</legend>
      {rows.map((r, i) => (
        // eslint-disable-next-line react/no-array-index-key
        <div key={i} className="action-picker__button-row">
          <label>
            <span>{`Button ${i + 1} label`}</span>
            <input value={r.label ?? ""} maxLength={80} onChange={(e) => edit(i, { label: e.target.value })} />
          </label>
          <label>
            <span>{`Button ${i + 1} style`}</span>
            <select value={r.style ?? "primary"} onChange={(e) => edit(i, { style: e.target.value })}>
              {BUTTON_STYLES.map((s) => (
                <option key={s} value={s}>
                  {cap(s)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{`Button ${i + 1} custom id`}</span>
            <input
              value={r.custom_id ?? ""}
              maxLength={Math.max(idLimit(r), (r.custom_id ?? "").length)}
              aria-invalid={buttonIdTooLong(r)}
              onChange={(e) => edit(i, { custom_id: e.target.value })}
            />
            {buttonIdTooLong(r) ? (
              <span className="notice notice--error" role="alert">
                {`Button ${i + 1} custom id must be at most ${MAX_VALUED_CUSTOM_ID} characters when it has a value`}
              </span>
            ) : null}
          </label>
          <ButtonValueField index={i} value={r.value} onChange={(v) => edit(i, { value: v })} />
          {r.value === undefined && r.code !== true ? null : (
            <label>
              <input
                type="checkbox"
                checked={r.code === true}
                onChange={(e) => edit(i, { code: e.target.checked || undefined })}
              />
              <span>{`Button ${i + 1} gets a one-time code`}</span>
            </label>
          )}
          {codeWithoutValue(r) ? (
            <span className="notice notice--error" role="alert">
              {`Button ${i + 1} one-time code needs a value`}
            </span>
          ) : null}
          <button type="button" onClick={() => save(rows.filter((_, j) => j !== i))}>
            {`Remove button ${i + 1}`}
          </button>
        </div>
      ))}
      <button
        type="button"
        disabled={rows.length >= MAX_BUTTONS}
        onClick={() => save([...rows, { label: "", style: "primary", custom_id: "" }])}
      >
        Add button
      </button>
    </fieldset>
  );
}

/** A single param's field: the command / args / choice selects, the headers rows, or a value. */
function ParamField({
  id,
  p,
  spec,
  params,
  actor,
  refs,
  setParam,
  setParams,
}: Readonly<ParamFieldsProps & { p: ActionParam }>) {
  const name = p.name;
  const commands = commandsOf(actor);
  if (p.widget === "command") {
    const command = typeof params[name] === "string" ? (params[name] as string) : "";
    // the arguments of the previous command go with it (the params whose args_for names this one)
    const argsCleared = Object.fromEntries(
      spec.params.filter((q) => q.widget === "command_args" && q.args_for === name).map((q) => [q.name, undefined]),
    );
    return (
      <label key={name}>
        <span id={`${id}-param-${name}`}>{labelOf(p)}</span>
        <select
          aria-labelledby={`${id}-param-${name}`}
          value={command}
          disabled={!actor}
          onChange={(e) => setParams({ ...params, ...argsCleared, [name]: e.target.value || undefined })}
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
  if (p.widget === "command_args") {
    const command = typeof params[p.args_for] === "string" ? (params[p.args_for] as string) : "";
    const declared = Object.keys(commands[command]?.params ?? {});
    const args = (params[name] ?? {}) as Record<string, unknown>;
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
          setParam(name, Object.keys(next).length ? next : undefined);
        }}
      />
    ));
  }
  const options = p.options_from ? actorChoices(actor, p.options_from) : choicesOf(p);
  if (options) {
    return <ChoiceField id={`${id}-param-${name}`} p={p} options={options} value={params[name]} onPick={(v) => setParam(name, v)} />;
  }
  if (p.type === "bool") {
    return (
      <label key={name} className="action-picker__check">
        <input
          type="checkbox"
          checked={params[name] === true}
          onChange={(e) => setParam(name, e.target.checked ? true : undefined)}
        />
        <span>{labelOf(p)}</span>
      </label>
    );
  }
  if (p.widget === "buttons") {
    return <ButtonsField value={params[name]} onChange={(v) => setParam(name, v)} />;
  }
  if (p.type === "dict" && p.widget !== "json") {
    return <HeadersField key={name} value={params[name]} onChange={(v) => setParam(name, v)} />;
  }
  return (
    <ValueField
      key={name}
      label={labelOf(p)}
      type={typeOf(p)}
      testKey={name}
      refs={refs}
      required={isRequired(p)}
      multiline={p.widget === "multiline"}
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
export default function ActionPicker(props: Readonly<Props>) {
  return props.catalogue ? <Picker {...props} catalogue={props.catalogue} /> : <FetchedPicker {...props} />;
}

/** The picker over the API's catalogue, with a loading and a failed state until it is there. */
function FetchedPicker(props: Readonly<Props>) {
  const { catalogue, failed, retry } = useActionCatalogue();
  if (catalogue) return <Picker {...props} catalogue={catalogue} />;
  return (
    <fieldset className="trigger-picker action-picker">
      <legend>Then what happens?</legend>
      {failed ? (
        <>
          <output className="trigger-picker__empty">Could not load the kinds of action the server offers.</output>
          <button type="button" className="btn" onClick={retry}>
            Try again
          </button>
        </>
      ) : (
        <output className="trigger-picker__words" aria-live="polite">
          Loading the kinds of action…
        </output>
      )}
    </fieldset>
  );
}

function Picker({
  value: stored,
  actors,
  triggerType,
  workflow,
  onChange,
  loadDiscordTargets = getDiscordTargets,
  catalogue,
}: Readonly<Props & { catalogue: ActionCatalogue }>) {
  const id = useId();
  const value = viewOf(stored ?? blankAction(catalogue), catalogue);
  const spec = kindOf(catalogue, value.kind);
  const kind = spec?.kind ?? value.kind;
  const params = (value.params ?? {}) as Record<string, unknown>;
  const eligible = actorsFor(kind, actors, catalogue);
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
    if (next !== kind) onChange(blankAction(catalogue, next, value.name));
  };

  const kindOptions = catalogue.items.filter((k) => offered(k, actors, catalogue) || k.kind === kind);
  const known = spec !== undefined;
  const extras = Object.entries(params).filter(([k]) => spec && !paramNamed(spec, k));
  const actorParam = paramNamed(spec, "actor");
  const showActor = actorParam !== undefined && actorParam.widget !== "hidden";

  return (
    <fieldset className="trigger-picker action-picker">
      <legend>Then what happens?</legend>
      <label>
        <span id={`${id}-kind`}>What happens</span>
        <select aria-labelledby={`${id}-kind`} value={known ? kind : value.kind} onChange={(e) => chooseKind(e.target.value)}>
          {known ? null : <option value={value.kind}>{value.kind}</option>}
          {kindOptions.map((k) => (
            <option key={k.kind} value={k.kind}>
              {k.label}
            </option>
          ))}
        </select>
      </label>
      {known ? null : (
        <p className="trigger-picker__words">
          This rule uses a “{value.kind}” action the editor cannot change; it is kept as it is.
        </p>
      )}
      {known && eligible.length === 0 && actorParam?.required ? (
        <output className="trigger-picker__empty">
          No enabled actor can do this yet. Add one on the Actors tab.
        </output>
      ) : null}

      {known && showActor ? (
        <label>
          <span id={`${id}-actor`}>Actor</span>
          <select
            aria-labelledby={`${id}-actor`}
            value={actorId}
            onChange={(e) =>
              setParams({
                ...params,
                actor: e.target.value || undefined,
                ...clearedWithActor(spec),
                channel: hasTargetPicker(spec) ? undefined : params.channel,
                ...actorCarryOver(spec, params, actors.find((a) => a.id === e.target.value)),
              })
            }
          >
            <option value="">Choose an actor…</option>
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
        <ParamFields
          id={id}
          load={loadDiscordTargets}
          spec={spec}
          params={params}
          actor={actor}
          refs={refs}
          setParam={setParam}
          setParams={setParams}
        />
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

      <label className="action-picker__check">
        <input
          type="checkbox"
          aria-describedby={`${id}-chain-end-hint`}
          checked={value.only_at_chain_end === true}
          onChange={(e) => {
            const next: Action = { ...value, only_at_chain_end: true };
            if (!e.target.checked) delete next.only_at_chain_end;
            onChange(next);
          }}
        />
        <span>Only where its chain ends</span>
      </label>
      <p id={`${id}-chain-end-hint`} className="trigger-picker__words">
        Skipped while another rule continues this run, so a chain of rules posts once.
      </p>

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
