import type {
  ErrorEnvelope,
  ItemList,
  Machine,
  Rule,
  RunSummary,
  Whoami,
  Workflow,
} from "./types";

/**
 * Same-origin API prefix. In dev, vite.config.ts proxies `/api` to the
 * culture-rules API (CULTURE_RULES_API_URL, default http://127.0.0.1:8765)
 * with the prefix stripped; in production whatever serves this bundle
 * mounts the API under the same prefix. Never an absolute remote URL.
 *
 * No request from this client attaches a credential: on the loopback
 * listener behind Cloudflare Access, the edge adds `Cf-Access-Jwt-Assertion`
 * to every same-origin request; in dev the vite proxy may add the dev
 * identity header (vite.config.ts). Who the caller is comes back from
 * `getWhoami`.
 */
export const API_ROOT = "/api";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export type Method = "GET" | "POST" | "PUT" | "DELETE";

/**
 * One call to the API: JSON in, JSON out, the error envelope turned into an
 * `ApiError`. Answers the parsed body, which may be `null` for an empty
 * answer. Every tab adapter builds on this (and `getJson`); none attaches a
 * credential.
 */
export async function request<T>(
  method: Method,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      method,
      signal,
      headers: {
        accept: "application/json",
        ...(body === undefined ? {} : { "content-type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
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

/** `GET path`; a 2xx answer that is not JSON is an `ApiError` (`not_json`). */
export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const body = await request<T | null>("GET", path, undefined, signal);
  if (body === null) {
    throw new ApiError(200, "not_json", `${path} did not return JSON`);
  }
  return body;
}

/** `GET path` of an `{items}` list route, unwrapped. */
export const items = async <T,>(path: string, signal?: AbortSignal): Promise<T[]> =>
  (await getJson<ItemList<T>>(path, signal)).items;

/** A query string from the defined params (`?a=1&b=2`, or "" when none). */
export function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const q = search.toString();
  return q ? `?${q}` : "";
}

export const listRules = (signal?: AbortSignal) => items<Rule>("/rules", signal);
export const listWorkflows = (signal?: AbortSignal) => items<Workflow>("/workflows", signal);
export const listMachines = (signal?: AbortSignal) => items<Machine>("/machines", signal);

export interface ListRunsParams {
  status?: string;
  rule_id?: string;
  /** Only runs of this workflow. */
  workflow_id?: string;
  /** Only runs with a step dispatched to this machine. */
  host?: string;
  limit?: number;
}

export const listRuns = (params: ListRunsParams = {}, signal?: AbortSignal) =>
  items<RunSummary>(`/runs${query({ ...params })}`, signal);

/** `GET /whoami`: who the API verified. A 401 means no credential reached it. */
export const getWhoami = (signal?: AbortSignal) => getJson<Whoami>("/whoami", signal);
