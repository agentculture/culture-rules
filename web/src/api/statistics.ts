import { ApiError, API_ROOT } from "./client";

/**
 * One machine's live state, for the Statistics lanes.
 *
 * PROPOSED endpoint — `GET /machines/status` is NOT in api/openapi.json yet.
 * `/machines` lists enrolled machines and `/health` is per-node, so nothing
 * today says whether a machine is online, how loaded it is, which steps it
 * runs or how deep its queue is. The tab asks for this shape and, when the
 * API answers 404/405 (endpoint absent), falls back to deriving what it can
 * from `/runs` + `/rules` (see statistics-view.ts `buildLanes`).
 */
export interface MachineStatus {
  name: string;
  online: boolean;
  /** Last heartbeat, ISO-8601; null when the machine never reported. */
  last_seen?: string | null;
  /** Percent 0-100 per resource; null = not reported (a CPU-only host has no GPU). */
  load: { cpu: number | null; gpu: number | null; mem: number | null } | null;
  running: { step: string; workflow: string }[];
  queue_depth: number;
}

/** `null` when the endpoint does not exist (404/405); other failures throw. */
export async function getMachineStatuses(signal?: AbortSignal): Promise<MachineStatus[] | null> {
  let response: Response;
  try {
    response = await fetch(`${API_ROOT}/machines/status`, {
      signal,
      headers: { accept: "application/json" },
    });
  } catch {
    throw new ApiError(0, "unreachable", `cannot reach the culture-rules API at ${API_ROOT}`);
  }
  if (response.status === 404 || response.status === 405) return null;
  if (!response.ok) {
    throw new ApiError(response.status, "http_error", `${response.status} ${response.statusText}`.trim());
  }
  const body = (await response.json().catch(() => null)) as { items?: MachineStatus[] } | null;
  if (!body || !Array.isArray(body.items)) {
    throw new ApiError(response.status, "not_json", "/machines/status did not return an item list");
  }
  return body.items;
}
