import { items, request } from "./client";

/**
 * Actor routes of the culture-rules HTTP API (api/openapi.json: `/actors`,
 * `/actors/{id}`, `/actors/{id}/enable|disable`), typed against
 * schemas/actor.schema.json. Built on client.ts's shared `request` helper;
 * like every call from this app, none attaches a credential.
 */
export type ActorKind = "agent" | "human" | "service" | "daemon" | "runner" | "robot" | "app";

/** `app` actor surfaces (culture_rules/model/app_actor.py `APP_SURFACES`). */
export type AppSurface = "github" | "jira" | "discord";

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
  guild_id?: string;
  channels?: string[];
}

/** `Actor.params` of an `app` actor. */
export interface AppActorParams {
  surface: AppSurface;
  /** Dotted lowercase event types the app emits (e.g. `github.pr.opened`). */
  events?: string[];
  probes?: AppProbe[];
  /** Action kinds it can perform. */
  actions?: string[];
  connection: GithubConnection | JiraConnection | DiscordConnection;
  /** Login of the App/bot/service account, used to tag self-authored events. */
  self_identity?: string;
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
export type ActorParams = Partial<AppActorParams> &
  RunnerActorParams & { http?: HttpPolicy } & Record<string, unknown>;

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
