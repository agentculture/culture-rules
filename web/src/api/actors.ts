import { items, request } from "./client";

/**
 * Actor routes of the culture-rules HTTP API (api/openapi.json: `/actors`,
 * `/actors/{id}`, `/actors/{id}/enable|disable`), typed against
 * schemas/actor.schema.json. Built on client.ts's shared `request` helper;
 * like every call from this app, none attaches a credential.
 */
export type ActorKind = "agent" | "human" | "service" | "daemon" | "runner" | "robot";

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
  params?: Record<string, unknown>;
  enabled?: boolean;
}

const enc = encodeURIComponent;

export const listActors = (signal?: AbortSignal) => items<Actor>("/actors", signal);

export const createActor = (actor: Actor) => request<Actor>("POST", "/actors", actor);

export const updateActor = (actor: Actor) => request<Actor>("PUT", `/actors/${enc(actor.id)}`, actor);

export const deleteActor = (id: string) => request<unknown>("DELETE", `/actors/${enc(id)}`);

export const setActorEnabled = (id: string, enabled: boolean) =>
  request<unknown>("POST", `/actors/${enc(id)}/${enabled ? "enable" : "disable"}`);
