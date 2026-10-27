import { ApiError, getJson, items, request } from "./client";
import type {
  ActorReferences,
  EnrolModelsRequest,
  EnrolModelsResult,
  RepointRequest,
  RepointResult,
} from "./types";

/**
 * Actor routes of the culture-rules HTTP API (api/openapi.json: `/actors`,
 * `/actors/{id}`, `/actors/{id}/enable|disable`), typed against
 * schemas/actor.schema.json. Built on client.ts's shared `request` helper;
 * like every call from this app, none attaches a credential.
 */
export type ActorKind = "agent" | "human" | "service" | "daemon" | "runner" | "robot" | "app";

/** `app` actor surfaces (culture_rules/model/app_actor.py `APP_SURFACES`). */
export type AppSurface = "github" | "jira" | "discord" | "email" | "search";

export interface AppProbe {
  name: string;
  command: string;
  schedule?: string;
}

/** Per-surface connection config; secret-looking keys hold `grant:NAME` references only. */
export interface GithubConnection {
  app_id?: string | number;
  installation_id?: string | number;
  private_key?: string;
  webhook_secret?: string;
  repos?: string[];
}
export interface JiraConnection {
  site?: string;
  email?: string;
  token?: string;
  webhook_token?: string;
  projects?: string[];
}
export interface DiscordConnection {
  bot_token?: string;
  /** One guild id, or several; unset or empty means every guild the bot is in. */
  guild_id?: string | string[];
  channels?: string[];
  /** The bot's application id (needed to register commands). */
  application_id?: string;
  /** The guilds commands are registered in: one id or several; unset means every guild id. */
  command_guild_id?: string | string[];
}

/** One option of a Discord slash command (culture_rules/model/app_actor.py, deviation d12). */
export interface DiscordCommandOption {
  /** `^[a-z_][a-z0-9_]{0,31}$`, never `interaction`, unique in its command. */
  name: string;
  /** 1–100 characters. */
  description: string;
  /** `string` (the default) or `message` (a message link or id; at most one per command). */
  kind?: "string" | "message";
  /** Default false; required options come before optional ones. */
  required?: boolean;
}

/** One Discord application command an app actor registers (`params.commands`, d12). */
export interface DiscordCommand {
  type: "slash" | "message";
  /** slash: `^[a-z0-9_-]{1,32}$`; message: 1–32 characters. Unique per type. */
  name: string;
  /** slash only, 1–100 characters. */
  description?: string;
  /** slash only, at most 5. */
  options?: DiscordCommandOption[];
  /** The private answer when no rule handles the command there, 1–200 characters. */
  not_here?: string;
}
/** The sender domains an `email` actor may send from (app_actor.py `EMAIL_DOMAINS`). */
export type EmailDomain = "culture.dev" | "jetson-ai-lab.org";
export interface EmailConnection {
  api_key?: string;
  /** Defaults to culture.dev. */
  domain?: EmailDomain;
  /** Defaults to no-reply. */
  local_part?: string;
  /** The recipient allow-list; empty sends to nobody. */
  recipients?: string[];
}
export interface SearchConnection {
  api_key?: string;
}

/** `Actor.params` of an `app` actor. */
export interface AppActorParams {
  surface: AppSurface;
  /** Dotted lowercase event types the app emits (e.g. `github.pr.opened`). */
  events?: string[];
  probes?: AppProbe[];
  /** Action kinds it can perform. */
  actions?: string[];
  connection: GithubConnection | JiraConnection | DiscordConnection | EmailConnection | SearchConnection;
  /** Login of the App/bot/service account, used to tag self-authored events. */
  self_identity?: string;
  /** discord: the application commands this bot registers (at most 10, at most 5 `message`). */
  commands?: DiscordCommand[];
}

/** One registered command of a runner actor (`params.commands[name]`). */
export interface RunnerCommand {
  argv: string[];
  /** Declared argument name -> port type (e.g. "string"). */
  params?: Record<string, string>;
  timeout?: number;
}

/** `Actor.params` of a runner actor. */
export interface RunnerActorParams {
  commands?: Record<string, RunnerCommand>;
}

/** The `params.http` policy of any actor an `http.call` action binds (an allowlist; none refuses all). */
export interface HttpPolicy {
  /** Exact hostnames and IP literals. */
  allow?: string[];
  /** Sent on every call; values may be `grant:NAME` references. */
  headers?: Record<string, string>;
}

/** `Actor.params` keys known to the editor; other keys ride along. */
export type ActorParams = Omit<Partial<AppActorParams>, "commands"> & {
  /** A runner's registered commands (by name), or a Discord app's application commands (a list). */
  commands?: NonNullable<RunnerActorParams["commands"] | AppActorParams["commands"]>;
  http?: HttpPolicy;
} & Record<string, unknown>;

export interface Actor {
  id: string;
  name: string;
  kind: ActorKind;
  description?: string;
  capabilities?: string[];
  harness?: string | null;
  model?: string | null;
  machine?: string | null;
  config_source?: "db" | "repo";
  repo?: string | null;
  params?: ActorParams;
  enabled?: boolean;
}

const enc = encodeURIComponent;

export const listActors = (signal?: AbortSignal) => items<Actor>("/actors", signal);

export const createActor = (actor: Actor) => request<Actor>("POST", "/actors", actor);

export const updateActor = (actor: Actor) => request<Actor>("PUT", `/actors/${enc(actor.id)}`, actor);

export const deleteActor = (id: string) => request<unknown>("DELETE", `/actors/${enc(id)}`);

export const setActorEnabled = (id: string, enabled: boolean) =>
  request<unknown>("POST", `/actors/${enc(id)}/${enabled ? "enable" : "disable"}`);

/**
 * `POST /actors/{id}/disable`. Without `force` a server that has the actor guards refuses
 * while an enabled workflow or rule still names the actor (409 `actor_in_use`, see
 * `inUseReferrers`); `force` disables it anyway.
 */
export const disableActor = (id: string, force = false) =>
  request<unknown>("POST", `/actors/${enc(id)}/disable${force ? "?force=true" : ""}`);

/** A text channel a Discord bot can post to; `visible` is false for a private channel it was not added to. */
export interface DiscordChannel {
  id: string;
  name: string;
  visible: boolean;
}

export interface DiscordGuild {
  id: string;
  name: string;
  channels: DiscordChannel[];
}

export interface DiscordTargets {
  guilds: DiscordGuild[];
}

/** `GET /actors/{id}/discord/targets`: the servers and channels a Discord app actor's bot can post to. */
export const getDiscordTargets = (actorId: string, signal?: AbortSignal) =>
  getJson<DiscordTargets>(`/actors/${enc(actorId)}/discord/targets`, signal);

/* ------------------------------------------------------------------ model actors (t10) */

/** `POST /actors/enrol-models` (admin): plan (`dry_run`, the default) or apply. */
export const enrolModels = (body: EnrolModelsRequest, signal?: AbortSignal) =>
  request<EnrolModelsResult>("POST", "/actors/enrol-models", body, signal);

/** `GET /actors/{id}/references`: the live workflow steps and rules naming the actor. */
export const getActorReferences = (id: string, signal?: AbortSignal) =>
  getJson<ActorReferences>(`/actors/${enc(id)}/references`, signal);

/**
 * `POST /workflows/{id}/repoint-actor` (editor), applied: every step action naming `from`
 * names `to`. A stale `expected_revision` is 409 `revision_conflict`; a trusted workflow is
 * 409 `workflow_trusted` unless `force`.
 */
export const repointWorkflowActor = (
  workflowId: string,
  body: Pick<RepointRequest, "from" | "to" | "expected_revision" | "force">,
  signal?: AbortSignal,
) =>
  request<RepointResult>(
    "POST",
    `/workflows/${enc(workflowId)}/repoint-actor`,
    { from: body.from, to: body.to, expected_revision: body.expected_revision, force: body.force ?? false, dry_run: false },
    signal,
  );

/** One allow-listed model gateway: a machine and its base URL. */
export interface ModelGateway {
  machine: string;
  url: string;
}

/**
 * The `model_gateways` variable's value as gateways: a list of `"machine=URL"` strings
 * (approved deviation d4). Any other form (an object, an item without `=`) is null, as the
 * server refuses it too.
 */
export function parseModelGateways(value: unknown): ModelGateway[] | null {
  if (!Array.isArray(value)) return null;
  const out: ModelGateway[] = [];
  for (const item of value) {
    if (typeof item !== "string" || !item.includes("=")) return null;
    const at = item.indexOf("=");
    out.push({ machine: item.slice(0, at).trim(), url: item.slice(at + 1) });
  }
  return out;
}

/** `GET /variables/model_gateways` as gateways; null when the variable is unset. */
export async function getModelGateways(signal?: AbortSignal): Promise<ModelGateway[] | null> {
  try {
    const variable = await getJson<{ value?: unknown }>("/variables/model_gateways", signal);
    const gateways = parseModelGateways(variable.value);
    if (gateways === null) {
      throw new ApiError(
        409,
        "invalid_model_gateways",
        'model_gateways must be a list of "machine=URL" strings',
      );
    }
    return gateways;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404 && err.code === "not_found") return null;
    throw err;
  }
}

/** What the web build's catch-all answers for an `/api` path that is no route (static.py). */
const NO_ROUTE_MESSAGES = new Set(["no such API route", "no such route"]);

/**
 * The route itself is missing: a server older than the model-actor routes (decision c33).
 * FastAPI's generic `{"detail": ...}` 404/405 carries no envelope (code `http_error`); with
 * the web build mounted, an unknown `/api` GET is a `not_found` envelope saying "no such API
 * route". A real `not_found` (an actor that does not exist) is neither.
 */
export function isMissingRoute(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false;
  if ((err.status === 404 || err.status === 405) && err.code === "http_error") return true;
  return err.status === 404 && err.code === "not_found" && NO_ROUTE_MESSAGES.has(err.message);
}

/** A workflow or rule an `actor_in_use` refusal names. */
export interface InUseReferrer {
  kind: "workflow" | "rule";
  id: string;
}

/**
 * The referrers of a 409 `actor_in_use` refusal (its `errors[].path` is `workflows/<id>` or
 * `rules/<id>`), each once and in order; null for any other failure.
 */
export function inUseReferrers(err: unknown): InUseReferrer[] | null {
  if (!(err instanceof ApiError) || err.code !== "actor_in_use") return null;
  const seen = new Set<string>();
  const out: InUseReferrer[] = [];
  for (const entry of err.errors) {
    const m = /^(workflows|rules)\/(.+)$/.exec(entry.path);
    if (!m || seen.has(entry.path)) continue;
    seen.add(entry.path);
    out.push({ kind: m[1] === "workflows" ? "workflow" : "rule", id: m[2] });
  }
  return out;
}
