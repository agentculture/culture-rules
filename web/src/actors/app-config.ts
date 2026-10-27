import type {
  ActorParams,
  AppActorParams,
  AppProbe,
  AppSurface,
  DiscordCommand,
  DiscordCommandOption,
  HttpPolicy,
  RunnerCommand,
} from "../api/actors";

/*
 * Pure model behind the Actors tab's app connection / declarations editor and
 * the runner commands editor. It mirrors the backend rules so a mistake is
 * caught before a request is sent: culture_rules/model/app_actor.py (surfaces,
 * dotted event types), model/action_kinds.py (action names),
 * actors/secrets.py (secret-looking keys hold only `grant:NAME`) and
 * actors/code.py + actors/inline_guard.py (argv templates, typed params).
 */

export const APP_SURFACES: readonly AppSurface[] = ["github", "jira", "discord", "email", "search"];

/** The sender domains an email app may send from, the default first (app_actor.py `EMAIL_DOMAINS`). */
export const EMAIL_DOMAINS: readonly string[] = ["culture.dev", "jetson-ai-lab.org"];

/** Action kinds an app can declare (culture_rules/model/action_kinds.py `ACTION_KINDS`). */
export const ACTION_KINDS: readonly { name: string; summary: string }[] = [
  { name: "noop", summary: "Do nothing" },
  { name: "message", summary: "Send a message on the mesh" },
  { name: "discord.message", summary: "Post a message on Discord" },
  { name: "discord.read", summary: "Read one Discord message in full (text and attachments)" },
  { name: "discord.react", summary: "React to a Discord message" },
  { name: "discord.reply", summary: "Answer a Discord slash or message command privately" },
  { name: "discord.sweep", summary: "Read a Discord channel's messages since the last run" },
  { name: "github.comment", summary: "Comment on a GitHub issue or PR" },
  { name: "github.push", summary: "Fast-forward a same-repo PR's head branch (never force)" },
  { name: "github.review_reply", summary: "Reply in a PR review thread, optionally resolving it" },
  { name: "jira.comment", summary: "Comment on a Jira issue" },
  { name: "http.call", summary: "Call an HTTP endpoint" },
  { name: "machine.command", summary: "Run a registered command on a machine" },
  { name: "email.send", summary: "Send an email through an email app" },
  { name: "search.web", summary: "Search the web through a search app" },
  { name: "llm.complete", summary: "Complete a locked brief with an OpenAI-compatible model (no tools)" },
  { name: "library.push", summary: "Upload a file (EPUB, Markdown, PDF, …) to a library (and queue it when the library has a device queue)" },
];

/** Lowercase dotted event types, at least two segments (app_actor.py `EVENT_TYPE_RE`). */
export const EVENT_TYPE_RE = /^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$/;
/** A secret reference (actors/secrets.py `_REF_RE`). */
export const GRANT_REF_RE = /^grant:[A-Za-z0-9][A-Za-z0-9._/-]*$/;

const SECRET_KEY_RE = /(?:^|[_-])(?:secret|token|password|passwd|api[_-]?key|credential|private[_-]?key)s?(?:[_-]|$)/i;
const BUDGET_KEY_RE = /(?:^|[_-])(?:budget|limit|max|min|count|num|quota|pct|warn)(?:[_-]|$)/i;
const CAMEL_HUMP_RE = /(?<=[a-z0-9])(?=[A-Z])/g;

/** Whether a param named `key` must hold a `grant:NAME` reference, never a literal. */
export function isSecretKey(key: string): boolean {
  const normalised = key.replace(CAMEL_HUMP_RE, "_");
  return SECRET_KEY_RE.test(normalised) && !BUDGET_KEY_RE.test(normalised);
}

export interface ConnectionField {
  key: string;
  label: string;
  /** A comma-separated list in the form, an array in the actor. */
  list?: boolean;
  /**
   * A comma-separated list in the form; one entry is written as a plain string (what an
   * older node reads), several as an array.
   */
  oneOrMany?: boolean;
  /** An enum: rendered as a select of exactly these values, the first one the default. */
  options?: readonly string[];
  /** Refused when blank. */
  required?: boolean;
  /** Written even when blank (a list as `[]`): the backend needs the key present. */
  keepEmpty?: boolean;
  /** Each list entry must be an email address. */
  emails?: boolean;
  /** Shown in an empty text field: the value the backend uses when it is left blank. */
  placeholder?: string;
}

const API_KEY: ConnectionField = { key: "api_key", label: "API key", required: true };

/** Per-surface connection fields, in the order the backend docstring gives them. */
export const CONNECTION_FIELDS: Record<AppSurface, ConnectionField[]> = {
  github: [
    { key: "app_id", label: "App id" },
    { key: "installation_id", label: "Installation id" },
    { key: "private_key", label: "Private key" },
    { key: "webhook_secret", label: "Webhook secret" },
    { key: "repos", label: "Repositories", list: true },
  ],
  jira: [
    { key: "site", label: "Site" },
    { key: "email", label: "Email" },
    { key: "token", label: "Token" },
    { key: "webhook_token", label: "Webhook token" },
    { key: "projects", label: "Projects", list: true },
  ],
  discord: [
    { key: "bot_token", label: "Bot token" },
    { key: "guild_id", label: "Guild ids", oneOrMany: true },
    { key: "channels", label: "Channels", list: true },
    { key: "application_id", label: "Application id (for commands)" },
    { key: "command_guild_id", label: "Command guild ids (unset: all guild ids)", oneOrMany: true },
  ],
  email: [
    API_KEY,
    { key: "domain", label: "Sender domain", options: EMAIL_DOMAINS },
    { key: "local_part", label: "Sender local part", placeholder: "no-reply" },
    { key: "recipients", label: "Recipients", list: true, keepEmpty: true, emails: true },
  ],
  search: [API_KEY],
};

/** A new connection for `surface`: every enum field set to its default. */
export function connectionDefaults(surface: AppSurface | ""): Record<string, string> {
  if (surface === "") return {};
  const enums = CONNECTION_FIELDS[surface].filter((f) => f.options?.length);
  return Object.fromEntries(enums.map((f) => [f.key, (f.options as readonly string[])[0]]));
}

export interface ProbeDraft {
  name: string;
  command: string;
  schedule: string;
}

/* ---------------- Discord application commands (d12) ---------------- */

export const DISCORD_COMMAND_TYPES = ["slash", "message"] as const;
export const DISCORD_OPTION_KINDS = ["string", "message"] as const;

export interface DiscordOptionDraft {
  name: string;
  description: string;
  kind: (typeof DISCORD_OPTION_KINDS)[number];
  required: boolean;
}

export interface DiscordCommandDraft {
  type: (typeof DISCORD_COMMAND_TYPES)[number];
  name: string;
  /** slash only; ignored (never saved) on a message command. */
  description: string;
  /** `not_here`: the private answer when no rule handles the command there. */
  notHere: string;
  /** slash only; ignored (never saved) on a message command. */
  options: DiscordOptionDraft[];
}

export const emptyDiscordCommand = (): DiscordCommandDraft => ({
  type: "slash",
  name: "",
  description: "",
  notHere: "",
  options: [],
});

export const emptyDiscordOption = (): DiscordOptionDraft => ({ name: "", description: "", kind: "string", required: false });

/** The limits app_actor.py enforces on `params.commands`. */
export const COMMAND_LIMITS = {
  commands: 10,
  messageCommands: 5,
  options: 5,
  name: 32,
  description: 100,
  notHere: 200,
} as const;
const SLASH_NAME_RE = /^[a-z0-9_-]{1,32}$/;
const OPTION_NAME_RE = /^[a-z_][a-z0-9_]{0,31}$/;
/** Length in characters as Python counts them (code points), not UTF-16 units. */
const charCount = (text: string) => [...text].length;

function discordCommandDraftFrom(c: DiscordCommand): DiscordCommandDraft {
  return {
    type: c.type,
    name: c.name ?? "",
    description: c.description ?? "",
    notHere: c.not_here ?? "",
    options: (c.options ?? []).map((o) => ({
      name: o.name ?? "",
      description: o.description ?? "",
      kind: o.kind ?? "string",
      required: o.required === true,
    })),
  };
}

function discordCommandFrom(d: DiscordCommandDraft): DiscordCommand {
  const command: DiscordCommand = { name: d.name.trim(), type: d.type };
  if (d.type === "slash") {
    command.description = d.description.trim();
    const options: DiscordCommandOption[] = d.options.map((o) => ({
      name: o.name.trim(),
      description: o.description.trim(),
      kind: o.kind,
      required: o.required,
    }));
    if (options.length) command.options = options;
  }
  const notHere = d.notHere.trim();
  if (notHere) command.not_here = notHere;
  return command;
}

function validateDiscordOptions(options: DiscordOptionDraft[], at: string, errors: FormErrors) {
  if (options.length > COMMAND_LIMITS.options) errors[`${at}.options`] = `A command takes at most ${COMMAND_LIMITS.options} options.`;
  const seen = new Set<string>();
  let messageKinds = 0;
  let optionalSeen = false;
  options.forEach((o, j) => {
    const key = `${at}.options.${j}`;
    const name = o.name.trim();
    if (!OPTION_NAME_RE.test(name)) {
      errors[`${key}.name`] = "Use lowercase letters, digits and underscores, up to 32, not starting with a digit.";
    } else if (name === "interaction") errors[`${key}.name`] = "interaction is reserved; choose another name.";
    else if (seen.has(name)) errors[`${key}.name`] = "Two options of this command share this name.";
    seen.add(name);
    const description = charCount(o.description.trim());
    if (description === 0) errors[`${key}.description`] = "An option needs a description.";
    else if (description > COMMAND_LIMITS.description) {
      errors[`${key}.description`] = `At most ${COMMAND_LIMITS.description} characters.`;
    }
    if (o.kind === "message" && ++messageKinds > 1) errors[`${key}.kind`] = "A command takes at most one message option.";
    if (o.required && optionalSeen) errors[`${key}.required`] = "Put required options before optional ones.";
    if (!o.required) optionalSeen = true;
  });
}

/** Refusals for `params.commands` (app_actor.py), keyed `commands`, `commands.<i>.<field>`, `commands.<i>.options.<j>.<field>`. */
export function validateDiscordCommands(commands: DiscordCommandDraft[]): FormErrors {
  const errors: FormErrors = {};
  const messages = commands.filter((c) => c.type === "message").length;
  if (commands.length > COMMAND_LIMITS.commands) errors.commands = `An app registers at most ${COMMAND_LIMITS.commands} commands.`;
  else if (messages > COMMAND_LIMITS.messageCommands) {
    errors.commands = `An app registers at most ${COMMAND_LIMITS.messageCommands} message commands.`;
  }
  const seen = { slash: new Set<string>(), message: new Set<string>() };
  commands.forEach((c, i) => {
    const at = `commands.${i}`;
    if (!DISCORD_COMMAND_TYPES.includes(c.type)) {
      errors[`${at}.type`] = "Choose slash or message.";
      return;
    }
    const name = c.name.trim();
    if (c.type === "slash" && !SLASH_NAME_RE.test(name)) {
      errors[`${at}.name`] = "Use lowercase letters, digits, - and _, up to 32.";
    } else if (c.type === "message" && (name === "" || charCount(name) > COMMAND_LIMITS.name)) {
      errors[`${at}.name`] = `Use 1 to ${COMMAND_LIMITS.name} characters.`;
    } else if (seen[c.type].has(name)) errors[`${at}.name`] = `Two ${c.type} commands share this name.`;
    seen[c.type].add(name);
    if (c.type === "slash") {
      const description = charCount(c.description.trim());
      if (description === 0) errors[`${at}.description`] = "A slash command needs a description.";
      else if (description > COMMAND_LIMITS.description) {
        errors[`${at}.description`] = `At most ${COMMAND_LIMITS.description} characters.`;
      }
      validateDiscordOptions(c.options, at, errors);
    }
    if (charCount(c.notHere.trim()) > COMMAND_LIMITS.notHere) errors[`${at}.not_here`] = `At most ${COMMAND_LIMITS.notHere} characters.`;
  });
  return errors;
}

export interface AppDraft {
  surface: AppSurface | "";
  /** Every connection value as text (lists comma separated). */
  connection: Record<string, string>;
  events: string[];
  actions: string[];
  probes: ProbeDraft[];
  selfIdentity: string;
  /** discord only: the application commands it registers (`params.commands`). */
  commands: DiscordCommandDraft[];
}

export const emptyAppDraft = (): AppDraft => ({
  surface: "",
  connection: {},
  events: [],
  actions: [],
  probes: [],
  selfIdentity: "",
  commands: [],
});

const splitList = (text: string): string[] => [
  ...new Set(
    text
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  ),
];

export function appDraftFrom(params: ActorParams | undefined): AppDraft {
  if (!params?.surface) return emptyAppDraft();
  const connection: Record<string, string> = {};
  for (const [key, value] of Object.entries((params.connection ?? {}) as Record<string, unknown>)) {
    if (Array.isArray(value)) connection[key] = value.join(", ");
    else if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") connection[key] = String(value);
  }
  return {
    surface: params.surface,
    connection,
    events: [...(params.events ?? [])],
    actions: [...(params.actions ?? [])],
    probes: (params.probes ?? []).map((p) => ({ name: p.name, command: p.command, schedule: p.schedule ?? "" })),
    selfIdentity: params.self_identity ?? "",
    commands: Array.isArray(params.commands) ? params.commands.map(discordCommandDraftFrom) : [],
  };
}

/** One connection field's stored value from its text, or undefined when it is left out. */
function connectionValue(field: ConnectionField, text: string): unknown {
  if (field.oneOrMany) {
    const items = splitList(text);
    if (!items.length) return undefined;
    return items.length === 1 ? items[0] : items; // one id stays a string (older nodes)
  }
  if (text === "" && !field.keepEmpty) return undefined;
  return field.list ? splitList(text) : text;
}

function probeFrom(p: AppDraft["probes"][number]): AppProbe {
  const probe: AppProbe = { name: p.name.trim(), command: p.command.trim() };
  const schedule = p.schedule.trim();
  if (schedule) probe.schedule = schedule;
  return probe;
}

/** The `app` params for a draft; blank connection fields are omitted. `base` keeps keys the form does not edit. */
export function appParamsFrom(draft: AppDraft, base: ActorParams | undefined): ActorParams {
  const surface = draft.surface as AppSurface;
  const known = new Set(CONNECTION_FIELDS[surface].map((f) => f.key));
  const baseConnection = base?.surface === surface ? (base.connection as Record<string, unknown>) : {};
  const connection: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(baseConnection)) if (!known.has(key)) connection[key] = value;
  for (const field of CONNECTION_FIELDS[surface]) {
    const value = connectionValue(field, (draft.connection[field.key] ?? "").trim());
    if (value !== undefined) connection[field.key] = value;
  }
  const probes = draft.probes.map(probeFrom);
  const next: ActorParams = {
    ...base,
    surface,
    connection: connection as AppActorParams["connection"],
    events: draft.events.map((e) => e.trim()),
    actions: [...draft.actions],
  };
  if (probes.length) next.probes = probes;
  else delete next.probes;
  if (draft.selfIdentity.trim()) next.self_identity = draft.selfIdentity.trim();
  else delete next.self_identity;
  // params.commands is Discord-only on an app actor; empty is left out
  if (surface === "discord" && draft.commands.length) next.commands = draft.commands.map(discordCommandFrom);
  else delete next.commands;
  return next;
}

export type FormErrors = Record<string, string>;

/**
 * A deliberately loose address check (app_actor.py `is_email_address` is the authority):
 * no whitespace, exactly one "@" with text before it, and a dot inside the domain with text
 * on both sides. Written without a regex so it runs in linear time on any input.
 */
export function isEmailAddress(text: string): boolean {
  if (/\s/.test(text)) return false;
  const at = text.indexOf("@");
  if (at <= 0 || at !== text.lastIndexOf("@")) return false;
  return text.slice(at + 2, -1).includes(".");
}

/** Why one connection value would be refused, or null. */
function connectionFieldError(field: ConnectionField, text: string): string | null {
  if (text === "") return field.required ? `${field.label} is required.` : null;
  if (isSecretKey(field.key) && !GRANT_REF_RE.test(text)) {
    return "Secrets are never typed here. Store it with the grant tool and enter its reference, like grant:NAME.";
  }
  if (field.options && !field.options.includes(text)) return `Choose one of ${field.options.join(", ")}.`;
  if (field.emails) {
    const bad = splitList(text).filter((item) => !isEmailAddress(item));
    if (bad.length) return `Not an email address: ${bad.join(", ")}.`;
  }
  return null;
}

/** Command guild ids must be ids, and among the guild ids when those are set (the node drops others). */
function commandGuildsError(guildIds: string, commandGuildIds: string): string | undefined {
  const wanted = splitList(commandGuildIds);
  if (wanted.some((id) => !/^\d+$/.test(id))) return "Use guild ids (numbers), separated with commas.";
  const listened = splitList(guildIds);
  if (listened.length > 0 && wanted.some((id) => !listened.includes(id))) {
    return "Use guild ids that are also in Guild ids.";
  }
  return undefined;
}

/** Client-side refusals for an app draft, keyed by field path. Empty means sendable. */
export function validateApp(draft: AppDraft): FormErrors {
  const errors: FormErrors = {};
  if (draft.surface === "") {
    errors.surface = "Choose the surface this app connects to.";
    return errors;
  }
  for (const field of CONNECTION_FIELDS[draft.surface]) {
    const error = connectionFieldError(field, (draft.connection[field.key] ?? "").trim());
    if (error) errors[`connection.${field.key}`] = error;
  }
  if (draft.surface === "discord") {
    const error = commandGuildsError(draft.connection.guild_id ?? "", draft.connection.command_guild_id ?? "");
    if (error) errors["connection.command_guild_id"] = error;
  }
  draft.events.forEach((event, i) => {
    if (!EVENT_TYPE_RE.test(event.trim())) {
      errors[`events.${i}`] = "Use lowercase dotted segments, at least two, like github.pr.opened.";
    }
  });
  draft.probes.forEach((probe, i) => {
    if (probe.name.trim() === "") errors[`probes.${i}.name`] = "A probe needs a name.";
    if (probe.command.trim() === "") errors[`probes.${i}.command`] = "A probe needs a command.";
  });
  if (draft.surface === "discord") Object.assign(errors, validateDiscordCommands(draft.commands));
  return errors;
}

/* ---------------- http policy ---------------- */

export interface HttpDraft {
  allow: string;
  headers: { name: string; value: string }[];
}

export const httpDraftFrom = (http: HttpPolicy | undefined): HttpDraft => ({
  allow: (http?.allow ?? []).join(", "),
  headers: Object.entries(http?.headers ?? {}).map(([name, value]) => ({ name, value })),
});

export function validateHttp(draft: HttpDraft): FormErrors {
  const errors: FormErrors = {};
  draft.headers.forEach((h, i) => {
    if (h.name.trim() === "") errors[`http.${i}.name`] = "A header needs a name.";
    else if (isSecretKey(h.name) || /^authorization$|^x-api-key$|^cookie$/i.test(h.name.trim())) {
      if (!GRANT_REF_RE.test(h.value.trim())) {
        errors[`http.${i}.value`] = "Credentials are never typed here. Enter a reference, like grant:NAME.";
      }
    }
  });
  return errors;
}

/** `params.http`, or undefined when the policy is empty (an unset policy refuses every call). */
export function httpParamsFrom(draft: HttpDraft): HttpPolicy | undefined {
  const allow = splitList(draft.allow);
  const headers = Object.fromEntries(
    draft.headers.filter((h) => h.name.trim() !== "").map((h) => [h.name.trim(), h.value.trim()]),
  );
  if (!allow.length && !Object.keys(headers).length) return undefined;
  return { ...(allow.length ? { allow } : {}), ...(Object.keys(headers).length ? { headers } : {}) };
}

/* ---------------- runner commands ---------------- */

/** Declared argument types (actors/code.py `_COERCIONS`). */
export const PARAM_TYPES = ["string", "int", "number", "bool"] as const;

export interface CommandDraft {
  name: string;
  /** One token per entry; `{param}` placeholders are bound from declared params. */
  argv: string[];
  params: { name: string; type: string }[];
  /** Seconds, as text; blank means the runner's default. */
  timeout: string;
}

export const emptyCommand = (): CommandDraft => ({ name: "", argv: [""], params: [], timeout: "" });

export function commandsFrom(params: ActorParams | undefined): CommandDraft[] {
  const commands = params?.commands;
  if (!commands || Array.isArray(commands)) return []; // a list is a Discord app's, not a runner's
  return Object.entries(commands).map(([name, spec]) => ({
    name,
    argv: [...(spec.argv ?? [])],
    params: Object.entries(spec.params ?? {}).map(([n, type]) => ({ name: n, type })),
    timeout: spec.timeout === undefined ? "" : String(spec.timeout),
  }));
}

export function commandsParamFrom(drafts: CommandDraft[]): Record<string, RunnerCommand> {
  const out: Record<string, RunnerCommand> = {};
  for (const d of drafts) {
    const spec: RunnerCommand = { argv: d.argv };
    if (d.params.length) spec.params = Object.fromEntries(d.params.map((p) => [p.name.trim(), p.type]));
    const timeout = Number(d.timeout);
    if (d.timeout.trim() !== "" && Number.isFinite(timeout)) spec.timeout = timeout;
    out[d.name.trim()] = spec;
  }
  return out;
}

const PLACEHOLDER = /\{(\w+)\}/g;
export const placeholdersOf = (argv: string[]): string[] => [
  ...new Set(argv.flatMap((token) => [...token.matchAll(PLACEHOLDER)].map((m) => m[1]))),
];

const SHELLS = new Set(["sh", "bash", "zsh", "dash", "ksh", "mksh", "ash", "fish", "csh", "tcsh"]);
const EVAL_FLAGS: Record<string, RegExp> = {
  python: /^-[A-Za-z]*c/,
  node: /^(-[A-Za-z]*[ep]|--eval|--print)/,
  perl: /^-[A-Za-z]*[eE]/,
  ruby: /^-[A-Za-z]*e/,
  php: /^-[A-Za-z]*r/,
  lua: /^-[A-Za-z]*e/,
};

/** `python3.12` -> `python`: drops trailing digits and dots (a linear scan, no backtracking regex). */
function stripVersionSuffix(name: string): string {
  let end = name.length;
  while (end > 0 && /[\d.]/.test(name[end - 1])) end--;
  return name.slice(0, end);
}

/** The EVAL_FLAGS key an interpreter's base name belongs to. */
function interpreterFamily(base: string): string {
  if (base === "python" || base === "pypy") return "python";
  if (base === "nodejs" || base === "bun") return "node";
  return base;
}

/** A warning when the template is a shell or interpreter told to evaluate code (the server refuses these at run time). */
export function inlineEvalWarning(argv: string[]): string | null {
  const tokens = argv.map((t) => t.trim()).filter(Boolean);
  const bases = tokens.map((t) => stripVersionSuffix(t.split("/").pop() ?? t));
  for (let i = 0; i < tokens.length; i++) {
    const base = bases[i];
    const rest = tokens.slice(i + 1);
    if (SHELLS.has(base) && rest.some((t) => /^-[A-Za-z]*c/.test(t) || t === "--command")) {
      return `"${tokens[i]}" with -c evaluates inline code; the server refuses it. Register a script instead.`;
    }
    const flags = EVAL_FLAGS[interpreterFamily(base)];
    if (flags && rest.some((t) => flags.test(t) || t === "--eval" || t === "--print")) {
      return `"${tokens[i]}" with an eval flag evaluates inline code; the server refuses it. Register a script instead.`;
    }
  }
  return null;
}

/** Client-side refusals, keyed `<i>.name`, `<i>.argv`, `<i>.params.<j>`, `<i>.timeout`. */
export function validateCommands(drafts: CommandDraft[]): FormErrors {
  const errors: FormErrors = {};
  const seen = new Set<string>();
  drafts.forEach((d, i) => {
    const name = d.name.trim();
    if (name === "") errors[`${i}.name`] = "A command needs a name.";
    else if (seen.has(name)) errors[`${i}.name`] = "Two commands share this name.";
    seen.add(name);
    if (d.argv.length === 0 || d.argv[0].trim() === "") errors[`${i}.argv`] = "The first token is the program to run.";
    const declared = new Set<string>();
    d.params.forEach((p, j) => {
      const pn = p.name.trim();
      if (!/^[A-Za-z_]\w*$/.test(pn)) errors[`${i}.params.${j}`] = "Use a plain name: letters, digits and underscores.";
      else if (declared.has(pn)) errors[`${i}.params.${j}`] = "This parameter is declared twice.";
      declared.add(pn);
    });
    const undeclared = placeholdersOf(d.argv).filter((p) => !declared.has(p));
    if (undeclared.length && !errors[`${i}.argv`]) {
      const names = undeclared.map((p) => "{" + p + "}").join(", ");
      errors[`${i}.argv`] = `Declare a parameter for ${names}.`;
    }
    const t = d.timeout.trim();
    if (t !== "" && !(Number.isFinite(Number(t)) && Number(t) > 0)) errors[`${i}.timeout`] = "Timeout is a number of seconds above zero.";
  });
  return errors;
}

export const hasErrors = (errors: FormErrors) => Object.keys(errors).length > 0;
