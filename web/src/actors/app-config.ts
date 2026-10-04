import type { ActorParams, AppActorParams, AppProbe, AppSurface, HttpPolicy, RunnerCommand } from "../api/actors";

/*
 * Pure model behind the Actors tab's app connection / declarations editor and
 * the runner commands editor. It mirrors the backend rules so a mistake is
 * caught before a request is sent: culture_rules/model/app_actor.py (surfaces,
 * dotted event types), model/action_kinds.py (action names),
 * actors/secrets.py (secret-looking keys hold only `grant:NAME`) and
 * actors/code.py + actors/inline_guard.py (argv templates, typed params).
 */

export const APP_SURFACES: readonly AppSurface[] = ["github", "jira", "discord"];

/** Action kinds an app can declare (culture_rules/model/action_kinds.py `ACTION_KINDS`). */
export const ACTION_KINDS: readonly { name: string; summary: string }[] = [
  { name: "noop", summary: "Do nothing" },
  { name: "message", summary: "Send a message" },
  { name: "github.comment", summary: "Comment on a GitHub issue or PR" },
  { name: "jira.comment", summary: "Comment on a Jira issue" },
  { name: "http.call", summary: "Call an HTTP endpoint" },
  { name: "machine.command", summary: "Run a registered command on a machine" },
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
}

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
    { key: "guild_id", label: "Guild id" },
    { key: "channels", label: "Channels", list: true },
  ],
};

export interface ProbeDraft {
  name: string;
  command: string;
  schedule: string;
}

export interface AppDraft {
  surface: AppSurface | "";
  /** Every connection value as text (lists comma separated). */
  connection: Record<string, string>;
  events: string[];
  actions: string[];
  probes: ProbeDraft[];
  selfIdentity: string;
}

export const emptyAppDraft = (): AppDraft => ({
  surface: "",
  connection: {},
  events: [],
  actions: [],
  probes: [],
  selfIdentity: "",
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
  };
}

/** The `app` params for a draft; blank connection fields are omitted. `base` keeps keys the form does not edit. */
export function appParamsFrom(draft: AppDraft, base: ActorParams | undefined): ActorParams {
  const surface = draft.surface as AppSurface;
  const known = new Set(CONNECTION_FIELDS[surface].map((f) => f.key));
  const baseConnection = base?.surface === surface ? (base.connection as Record<string, unknown>) : {};
  const connection: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(baseConnection)) if (!known.has(key)) connection[key] = value;
  for (const field of CONNECTION_FIELDS[surface]) {
    const text = (draft.connection[field.key] ?? "").trim();
    if (text === "") continue;
    connection[field.key] = field.list ? splitList(text) : text;
  }
  const probes: AppProbe[] = draft.probes.map((p) => {
    const probe: AppProbe = { name: p.name.trim(), command: p.command.trim() };
    const schedule = p.schedule.trim();
    if (schedule) probe.schedule = schedule;
    return probe;
  });
  const next: ActorParams = {
    ...(base ?? {}),
    surface,
    connection: connection as AppActorParams["connection"],
    events: draft.events.map((e) => e.trim()),
    actions: [...draft.actions],
  };
  if (probes.length) next.probes = probes;
  else delete next.probes;
  if (draft.selfIdentity.trim()) next.self_identity = draft.selfIdentity.trim();
  else delete next.self_identity;
  return next;
}

export type FormErrors = Record<string, string>;

/** Client-side refusals for an app draft, keyed by field path. Empty means sendable. */
export function validateApp(draft: AppDraft): FormErrors {
  const errors: FormErrors = {};
  if (draft.surface === "") {
    errors.surface = "Choose the surface this app connects to.";
    return errors;
  }
  for (const field of CONNECTION_FIELDS[draft.surface]) {
    const text = (draft.connection[field.key] ?? "").trim();
    if (isSecretKey(field.key) && text !== "" && !GRANT_REF_RE.test(text)) {
      errors[`connection.${field.key}`] =
        "Secrets are never typed here. Store it with the grant tool and enter its reference, like grant:NAME.";
    }
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
  return Object.entries(params?.commands ?? {}).map(([name, spec]) => ({
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
