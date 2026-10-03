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

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      signal,
      headers: { accept: "application/json" },
    });
  } catch {
    throw new ApiError(0, "unreachable", `cannot reach the culture-rules API at ${API_ROOT}`);
  }
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!response.ok) {
    const envelope = body as Partial<ErrorEnvelope> | null;
    throw new ApiError(
      response.status,
      envelope?.error?.code ?? "http_error",
      envelope?.error?.message ?? `${response.status} ${response.statusText}`.trim(),
    );
  }
  if (body === null) {
    throw new ApiError(response.status, "not_json", `${path} did not return JSON`);
  }
  return body as T;
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const q = search.toString();
  return q ? `?${q}` : "";
}

const items = async <T>(path: string, signal?: AbortSignal) =>
  (await getJson<ItemList<T>>(path, signal)).items;

export const listRules = (signal?: AbortSignal) => items<Rule>("/rules", signal);
export const listWorkflows = (signal?: AbortSignal) => items<Workflow>("/workflows", signal);
export const listMachines = (signal?: AbortSignal) => items<Machine>("/machines", signal);

export interface ListRunsParams {
  status?: string;
  rule_id?: string;
  limit?: number;
}

export const listRuns = (params: ListRunsParams = {}, signal?: AbortSignal) =>
  items<RunSummary>(`/runs${query({ ...params })}`, signal);

/** `GET /whoami`: who the API verified. A 401 means no credential reached it. */
export const getWhoami = (signal?: AbortSignal) => getJson<Whoami>("/whoami", signal);
