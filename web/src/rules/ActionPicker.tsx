import { useId, useState } from "react";
import { getDiscordTargets } from "../api/actors";
import { useDiscordTargets, type LoadTargets } from "./useDiscordTargets";
import type { Actor, HttpPolicy, RunnerActorParams } from "../api/actors";
import type { Action, Workflow } from "../api/types";

type ParamType = "str" | "int" | "number" | "bool" | "dict" | "list" | "any";

interface Choice {
  value: string;
  label: string;
}

interface ParamSpec {
  type: ParamType;
  required?: boolean;
  /** A closed set of values, picked from a select whose empty option reads `choose`. */
  options?: readonly Choice[];
  /** Like `options`, but the values are the list in the chosen actor's `params[optionsFrom]`. */
  optionsFrom?: string;
  choose?: string;
}

interface KindSpec {
  label: string;
  params: Record<string, ParamSpec>;
  /** Groups of params of which at least one must be set (the catalog's `any_of`). */
  anyOf?: readonly (readonly string[])[];
}

const S: ParamSpec = { type: "str" };
const RS: ParamSpec = { type: "str", required: true };
const I: ParamSpec = { type: "int" };
const RI: ParamSpec = { type: "int", required: true };

const METHODS: readonly Choice[] = ["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => ({ value: m, label: m }));
/** Brave's freshness windows (search.web `freshness`). */
/** llm.complete's locked briefs. */
const BRIEFS: readonly Choice[] = [
  { value: "newspaper", label: "Newspaper" },
  { value: "transcript", label: "Transcript" },
  { value: "recording", label: "Recording: summary, key facts, action items" },
];
/** transcript.render's output formats. */
const RENDER_FORMATS: readonly Choice[] = [
  { value: "epub", label: "EPUB book" },
  { value: "md", label: "Markdown" },
];
const FRESHNESS: readonly Choice[] = [
  { value: "pd", label: "Past day" },
  { value: "pw", label: "Past week" },
  { value: "pm", label: "Past month" },
  { value: "py", label: "Past year" },
];

/**
 * The action catalogue, mirroring culture_rules/model/action_kinds.py: the
 * params each kind takes, their types and which are required. Order is the
 * order the picker lists them in.
 */
export const ACTION_KINDS: Record<string, KindSpec> = {
  noop: { label: "Do nothing", params: {} },
  message: { label: "Send a message on the mesh", params: { channel: RS, text: RS } },
  "discord.message": {
    label: "Post a message on Discord",
    params: { actor: RS, guild: S, channel: RS, text: RS },
  },
  "discord.read": {
    label: "Read a Discord message in full",
    params: { actor: RS, guild: S, channel: RS, message: RS },
  },
  "discord.react": {
    label: "React to a Discord message",
    params: { actor: RS, guild: S, channel: RS, message: RS, emoji: RS },
  },
  "discord.reply": {
    label: "Answer a Discord command privately",
    params: { actor: RS, event_id: RS, text: RS },
  },
  "discord.sweep": {
    label: "Read a Discord channel since the last run",
    params: {
      actor: RS,
      guild: S,
      channel: RS,
      cursor_key: RS,
      limit: I,
      include_bots: { type: "bool" },
      backfill: { type: "bool" },
    },
  },
  "github.comment": {
    label: "Comment on GitHub",
    params: { actor: RS, repo: RS, number: RI, body: RS },
  },
  "github.push": {
    label: "Push a fix to a GitHub PR",
    params: {
      actor: RS,
      repo: RS,
      number: RI,
      head_branch: RS,
      expected_head_sha: RS,
      commit_sha: RS,
      source: RS,
    },
  },
  "github.review_reply": {
    label: "Reply in a GitHub review thread",
    params: {
      actor: RS,
      repo: RS,
      number: RI,
      comment_id: RI,
      body: RS,
      thread_id: S,
      resolve: { type: "bool" },
    },
  },
  "jira.comment": { label: "Comment on Jira", params: { actor: RS, issue: RS, body: RS } },
  "http.call": {
    label: "Call an HTTP endpoint",
    params: {
      actor: RS,
      method: { ...RS, options: METHODS, choose: "Choose a method…" },
      url: RS,
      headers: { type: "dict" },
      body: { type: "any" },
    },
  },
  "machine.command": {
    label: "Run a command on a machine",
    params: { actor: RS, command: RS, args: { type: "dict" } },
  },
  "email.send": {
    label: "Send an email",
    params: { actor: RS, to: { type: "list", required: true }, subject: RS, html: S, text: S },
  },
  "search.web": {
    label: "Search the web",
    params: {
      actor: RS,
      query: RS,
      count: I,
      freshness: { ...S, options: FRESHNESS, choose: "Any time" },
      country: S,
      lang: S,
    },
  },
  "llm.complete": {
    label: "Summarise with a language model",
    params: {
      actor: RS,
      brief: { ...S, options: BRIEFS, choose: "Newspaper (default)" },
      content: { type: "any" },
      content_path: S,
      content_root: S,
      content_max_chars: I,
      max_tokens: I,
      temperature: { type: "number" },
      not_after: S,
      cache: { type: "bool" },
    },
    anyOf: [["content", "content_path"]],
  },
  "audio.transcribe": {
    label: "Transcribe an audio file",
    params: { actor: RS, audio_path: RS, audio_sha256: S, output_dir: RS, language: S, model: S },
  },
  "transcript.render": {
    label: "Make a book or Markdown from a transcript",
    params: {
      actor: S,
      result_path: RS,
      root: RS,
      recorded_at: RS,
      language: S,
      summary: { type: "any" },
      format: { ...S, options: RENDER_FORMATS, choose: "EPUB book (default)" },
    },
  },
  "decider.assess": {
    label: "Ask the decider",
    params: { actor: RS, state: RS, questions: { type: "any", required: true }, not_after: S },
  },
  "library.push": {
    label: "Send a book to a library",
    params: {
      actor: RS,
      content_base64: S,
      content_path: S,
      content_root: S,
      title: RS,
      kind: { ...RS, optionsFrom: "kinds", choose: "Choose a kind…" },
      visibility: { ...S, optionsFrom: "visibilities", choose: "The library's default" },
      format: { ...S, optionsFrom: "formats", choose: "EPUB (default)" },
      item_id: I,
      author: S,
      device_id: S,
    },
    anyOf: [["content_base64", "content_path"]],
  },
};

/** Checkbox labels that read better than the param name. */
const BOOL_LABELS: Record<string, string> = {
  resolve: "Resolve the thread",
  cache: "Keep the first answer for this file",
};

/** Params shown as a textarea: long text. */
const MULTILINE = new Set(["body", "text", "html", "content", "state"]);

/** Legacy names the backend accepts as aliases of a catalogued kind. */
const ALIASES: Record<string, string> = { "mesh.message": "message" };
const canonical = (kind: string) => ALIASES[kind] ?? kind;

const REFERENCE = /^(trigger|workflow|vars|rule)\.[A-Za-z0-9_.]+$/;
const CUSTOM = "__custom__";

/** Common `trigger.data.<field>` names per trigger type prefix; anything else uses a custom path. */
const TRIGGER_FIELDS: { prefix: string; fields: string[] }[] = [
  { prefix: "github.", fields: ["number", "repository", "title", "author", "url"] },
  { prefix: "jira.", fields: ["key", "project", "summary", "status", "url"] },
  { prefix: "discord.", fields: ["channel_id", "guild_id", "content", "author_name", "message_id"] },
];

/**
 * How the picker shows a stored action: a `message` that still names an actor is the form a
 * Discord message had before `discord.message` existed, so it is shown (and saved) as one.
 */
export function viewOf(action: Action): Action {
  const actor = (action.params as Record<string, unknown> | undefined)?.actor;
  if (canonical(action.kind) === "message" && typeof actor === "string" && actor) {
    return { ...action, kind: "discord.message" };
  }
  return action;
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

function typeOk({ type, options }: ParamSpec, value: unknown): boolean {
  if (isDynamic(value) || type === "any") return true;
  if (options) return options.some((o) => o.value === value);
  if (type === "int") return typeof value === "number" && Number.isInteger(value);
  if (type === "number") return typeof value === "number" && Number.isFinite(value);
  if (type === "str") return typeof value === "string";
  if (type === "bool") return typeof value === "boolean";
  if (type === "list") return Array.isArray(value);
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
export function actionProblem(stored: Action): "no_actor_port" | "empty" | "invalid_value" | null {
  const action = viewOf(stored);
  const spec = ACTION_KINDS[canonical(action.kind)];
  if (!spec || action.kind === "mesh.message") return null;
  const params = (action.params ?? {}) as Record<string, unknown>;
  if (spec.params.actor?.required && isEmpty(params.actor)) return "no_actor_port";
  for (const [name, p] of Object.entries(spec.params)) {
    if (name !== "actor" && p.required && isEmpty(params[name])) return "empty";
  }
  for (const group of spec.anyOf ?? []) {
    if (group.every((name) => isEmpty(params[name]))) return "empty";
  }
  for (const [name, p] of Object.entries(spec.params)) {
    if (!isEmpty(params[name]) && !typeOk(p, params[name])) return "invalid_value";
  }
  return null;
}

const enabled = (a: Actor) => a.enabled !== false;
const declares = (a: Actor, kind: string) =>
  a.kind === "app" && enabled(a) && (a.params?.actions ?? []).includes(kind);
const allowsHttp = (a: Actor) => enabled(a) && ((a.params?.http as HttpPolicy | undefined)?.allow ?? []).length > 0;
const commandsOf = (a?: Actor) => (a?.params as RunnerActorParams | undefined)?.commands ?? {};
const onSurface = (a: Actor, surface: string) => a.kind === "app" && enabled(a) && a.params?.surface === surface;
const hasBaseUrl = (a: Actor) => enabled(a) && typeof a.params?.base_url === "string" && a.params.base_url !== "";
const hasTranscriptsRoot = (a: Actor) =>
  enabled(a) && ["transcripts_root", "output_root"].some((k) => typeof a.params?.[k] === "string" && a.params[k] !== "");

/**
 * Enabled actors able to perform `kind`: app actors by declared actions (email and search by
 * surface), runners by command, an OpenAI-compatible agent (one with a `base_url`) for
 * llm.complete, audio.transcribe and decider.assess, and any other actor with a `base_url` (the library service) for library.push.
 * transcript.render needs no actor; one with a transcripts or output root may narrow where it reads.
 */
export function actorsFor(kind: string, actors: Actor[]): Actor[] {
  switch (canonical(kind)) {
    case "machine.command":
      return actors.filter((a) => a.kind === "runner" && enabled(a));
    case "http.call":
      return actors.filter((a) => declares(a, "http.call") || allowsHttp(a));
    case "email.send":
      return actors.filter((a) => onSurface(a, "email"));
    case "search.web":
      return actors.filter((a) => onSurface(a, "search"));
    case "llm.complete":
      return actors.filter((a) => a.kind === "agent" && hasBaseUrl(a));
    case "audio.transcribe":
    case "decider.assess":
      return actors.filter((a) => a.kind === "agent" && hasBaseUrl(a));
    case "library.push":
      return actors.filter((a) => a.kind !== "agent" && a.kind !== "app" && hasBaseUrl(a));
    case "transcript.render":
      return actors.filter(hasTranscriptsRoot);
    case "noop":
      return [];
    default:
      return actors.filter((a) => declares(a, canonical(kind)));
  }
}

/** Discord kinds whose server and channel are picked from what the bot can see. */
const DISCORD_TARGET_KINDS = new Set(["discord.message", "discord.read", "discord.react", "discord.sweep"]);

/** The mesh message, noop and transcript.render need no actor; every other kind needs one enabled actor that supports it. */
const offered = (kind: string, actors: Actor[]) =>
  kind === "noop" || kind === "message" || kind === "transcript.render" || actorsFor(kind, actors).length > 0;

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
  if (type === "list") {
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
  value: Action;
  actors: Actor[];
  /** The rule's trigger event type, for the common trigger fields offered as mappings. */
  triggerType?: string;
  /** The rule's workflow, whose outputs can be mapped. */
  workflow?: Workflow;
  onChange: (action: Action) => void;
  /** Where a Discord message's servers and channels come from (default: the API). */
  loadDiscordTargets?: LoadTargets;
}

const cap = (s: string) => {
  const words = s.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
};

interface ParamFieldsProps {
  id: string;
  kind: string;
  load: LoadTargets;
  spec: KindSpec;
  params: Record<string, unknown>;
  actor: Actor | undefined;
  refs: MappingRefs;
  setParam: (key: string, v: unknown) => void;
  setParams: (next: Record<string, unknown>) => void;
}

/** One typed field per param of the chosen action kind (the actor select is rendered separately). */
function ParamFields(props: Readonly<ParamFieldsProps>) {
  const discord = DISCORD_TARGET_KINDS.has(props.kind);
  const actorId = typeof props.params.actor === "string" ? props.params.actor : "";
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
      {Object.entries(props.spec.params)
        .filter(([name]) => name !== "actor" && !(discord && (name === "guild" || name === "channel")))
        .filter(([name]) => !hiddenForActor(props.kind, name, props.actor))
        .map(([name, p]) => (
          <ParamField key={name} name={name} p={p} {...props} />
        ))}
    </>
  );
}

interface ChoiceFieldProps {
  id: string;
  name: string;
  p: ParamSpec;
  value: unknown;
  onPick: (value: string | undefined) => void;
}

/**
 * library.push params the chosen actor makes moot: `device_id` without a `queue_path` (the
 * library has no device queue), `visibility` when the actor lists no visibilities.
 */
function hiddenForActor(kind: string, name: string, actor: Actor | undefined): boolean {
  if (kind !== "library.push" || !actor) return false;
  if (name === "device_id") return actor.params?.queue_path == null;
  if (name === "visibility") return actorChoices(actor, "visibilities").length === 0;
  return false;
}

/**
 * On an actor change, the library.push values the new actor makes moot or refuses: a
 * `device_id` it has no `queue_path` for, a `kind` or `visibility` it does not list (#91 review).
 * Hidden values would otherwise stay saved where the step can no longer show or fix them.
 */
function libraryCarryOver(
  kind: string,
  params: Record<string, unknown>,
  actor: Actor | undefined,
): Record<string, unknown> {
  if (kind !== "library.push") return {};
  const listed = (key: string, value: unknown) =>
    actorChoices(actor, key).some((c) => c.value === value) ? value : undefined;
  return {
    device_id: actor?.params?.queue_path == null ? undefined : params.device_id,
    kind: listed("kinds", params.kind),
    visibility: listed("visibilities", params.visibility),
    format: listed("formats", params.format),
  };
}

/** The string list an actor's `params[key]` holds (library.push `kinds`), as choices. */
function actorChoices(actor: Actor | undefined, key: string): Choice[] {
  const list = actor?.params?.[key];
  if (!Array.isArray(list)) return [];
  return list.filter((v): v is string => typeof v === "string").map((v) => ({ value: v, label: cap(v) }));
}

/** A param with a closed set of values (http.call `method`, search.web `freshness`) as a select. */
function ChoiceField({ id, name, p, value, onPick }: Readonly<ChoiceFieldProps>) {
  const current = typeof value === "string" ? value : "";
  const options = p.options ?? [];
  return (
    <label>
      <span id={id}>{cap(name)}</span>
      <select aria-labelledby={id} value={current} onChange={(e) => onPick(e.target.value || undefined)}>
        <option value="">{p.choose ?? "Choose…"}</option>
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

/** A single param's field: the command / args / choice selects, the headers rows, or a value. */
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
  if (p.optionsFrom) {
    const fromActor = { ...p, options: actorChoices(actor, p.optionsFrom) };
    return <ChoiceField id={`${id}-param-${name}`} name={name} p={fromActor} value={params[name]} onPick={(v) => setParam(name, v)} />;
  }
  if (p.options) {
    return <ChoiceField id={`${id}-param-${name}`} name={name} p={p} value={params[name]} onPick={(v) => setParam(name, v)} />;
  }
  if (p.type === "bool") {
    return (
      <label key={name} className="action-picker__check">
        <input
          type="checkbox"
          checked={params[name] === true}
          onChange={(e) => setParam(name, e.target.checked ? true : undefined)}
        />
        <span>{BOOL_LABELS[name] ?? cap(name)}</span>
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
      multiline={MULTILINE.has(name)}
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
export default function ActionPicker({
  value: stored,
  actors,
  triggerType,
  workflow,
  onChange,
  loadDiscordTargets = getDiscordTargets,
}: Readonly<Props>) {
  const id = useId();
  const value = viewOf(stored);
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
            onChange={(e) =>
              setParams({
                ...params,
                actor: e.target.value || undefined,
                command: undefined,
                args: undefined,
                guild: undefined,
                channel: DISCORD_TARGET_KINDS.has(kind) ? undefined : params.channel,
                ...libraryCarryOver(kind, params, actors.find((a) => a.id === e.target.value)),
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
          kind={kind}
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
