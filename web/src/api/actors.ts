import { API_ROOT, ApiError } from "./client";
import type { ErrorEnvelope, ItemList } from "./types";

/**
 * Actor routes of the culture-rules HTTP API (api/openapi.json: `/actors`,
 * `/actors/{id}`, `/actors/{id}/enable|disable`), typed against
 * schemas/actor.schema.json. Built on client.ts's `API_ROOT` and `ApiError`;
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

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      method,
      signal,
      headers: {
        accept: "application/json",
        ...(body !== undefined ? { "content-type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "unreachable", `cannot reach the culture-rules API at ${API_ROOT}`);
  }
  const text = await response.text();
  let parsed: unknown = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = null;
  }
  if (!response.ok) {
    const envelope = parsed as Partial<ErrorEnvelope> | null;
    throw new ApiError(
      response.status,
      envelope?.error?.code ?? "http_error",
      envelope?.error?.message ?? `${response.status} ${response.statusText}`.trim(),
    );
  }
  return parsed as T;
}

const enc = encodeURIComponent;

export const listActors = async (signal?: AbortSignal) =>
  (await request<ItemList<Actor>>("GET", "/actors", undefined, signal)).items;

export const createActor = (actor: Actor) => request<Actor>("POST", "/actors", actor);

export const updateActor = (actor: Actor) => request<Actor>("PUT", `/actors/${enc(actor.id)}`, actor);

export const deleteActor = (id: string) => request<unknown>("DELETE", `/actors/${enc(id)}`);

export const setActorEnabled = (id: string, enabled: boolean) =>
  request<unknown>("POST", `/actors/${enc(id)}/${enabled ? "enable" : "disable"}`);
