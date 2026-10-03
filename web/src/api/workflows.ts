/**
 * Workflows-tab API calls and types, built on client.ts's exported
 * `API_ROOT` and `ApiError` (client.ts and types.ts are shared and stay
 * untouched by the tabs). Shapes follow the committed api/openapi.json and
 * schemas/workflow.schema.json; run documents follow the engine's persisted
 * run state (culture_rules/engine/runs.py `_new_state`).
 *
 * Every call except `listRepos` is a route in api/openapi.json. `GET /repos`
 * is PLANNED — no repository endpoint exists yet; the repo picker calls it
 * and degrades to "no repositories" on a 404.
 */
import { API_ROOT, ApiError } from "./client";
import type { ErrorEnvelope, ItemList, Placement, RunSummary } from "./types";

export type PortType = "string" | "number" | "integer" | "boolean" | "object" | "array" | "any";

export const PORT_TYPES: readonly PortType[] = [
  "string",
  "number",
  "integer",
  "boolean",
  "object",
  "array",
  "any",
];

export type StepKind = "logic" | "ai" | "code" | "actor_task" | "for_each" | "retry_until";

export const STEP_KINDS: readonly StepKind[] = [
  "logic",
  "ai",
  "code",
  "actor_task",
  "for_each",
  "retry_until",
];

/** A typed input or output port (workflow.schema.json `Port`). */
export interface Port {
  name: string;
  type?: PortType;
  required?: boolean;
  description?: string;
}

export interface Step {
  id: string;
  name?: string;
  description?: string;
  kind: StepKind;
  inputs?: Port[];
  outputs?: Port[];
  placement?: Placement | null;
  timeout_s?: number | null;
  retry?: Record<string, unknown> | null;
  config?: Record<string, unknown>;
  max_iterations?: number | null;
  body?: Step[];
  enabled?: boolean;
}

/** Port-to-port wiring; `source` may be `inputs` (the workflow's inputs). */
export interface WorkflowEdge {
  source: string;
  source_port: string;
  target: string;
  target_port: string;
}

export interface WorkflowOutput {
  name: string;
  type?: PortType;
  /** inputs.<n>, vars.<n> or steps.<id>.outputs.<port> */
  source?: string | null;
}

/** A full workflow definition (schemas/workflow.schema.json). */
export interface WorkflowDef {
  id: string;
  name: string;
  description?: string;
  version?: number;
  inputs?: Port[];
  variables?: { name: string; type?: PortType; default?: unknown }[];
  steps?: Step[];
  edges?: WorkflowEdge[];
  outputs?: WorkflowOutput[];
  enabled?: boolean;
  schema_version?: string;
}

/** The fields of schemas/workflow.schema.json; a PUT body carries only these. */
export const WORKFLOW_FIELDS = [
  "id",
  "name",
  "description",
  "version",
  "inputs",
  "variables",
  "steps",
  "edges",
  "outputs",
  "enabled",
  "schema_version",
] as const;

export interface Actor {
  id: string;
  name?: string;
  kind?: string;
  machine?: string | null;
  harness?: string | null;
  model?: string | null;
  capabilities?: string[];
  enabled?: boolean;
}

/** One step's persisted state inside a run document. */
export interface RunStepState {
  key: string;
  def: string;
  loop?: unknown;
  status: string;
  attempt?: number;
  host: string | null;
  error?: unknown;
}

/** `GET /runs/{id}`: the persisted run document. */
export interface RunDoc {
  id: string;
  status: string;
  rule?: { id: string } | null;
  workflow?: { id: string; version?: number; definition?: WorkflowDef } | null;
  steps: RunStepState[];
  created_at?: string | null;
  finished_at?: string | null;
  error?: unknown;
}

export interface ImportChange {
  kind: string;
  id: string;
  path: string;
  action: string;
}

export interface ImportPlan {
  applied: boolean;
  changes: ImportChange[];
  errors?: { path: string; code: string; message: string }[];
}

export interface ExportResult {
  format: string;
  files: Record<string, string>;
}

/** PLANNED `GET /repos` item: a definitions repository the API can reach. */
export interface Repo {
  name: string;
  url?: string;
}

async function request<T>(
  method: "GET" | "POST" | "PUT",
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  const headers: Record<string, string> = { accept: "application/json" };
  if (body !== undefined) headers["content-type"] = "application/json";
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      method,
      signal,
      headers,
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
  if (parsed === null) {
    throw new ApiError(response.status, "not_json", `${path} did not return JSON`);
  }
  return parsed as T;
}

const enc = encodeURIComponent;

export const listWorkflowDefs = async (signal?: AbortSignal) =>
  (await request<ItemList<WorkflowDef>>("GET", "/workflows", undefined, signal)).items;

export const getWorkflowDef = (id: string, signal?: AbortSignal) =>
  request<WorkflowDef>("GET", `/workflows/${enc(id)}`, undefined, signal);

/** `PUT /workflows/{id}`: replace the definition (only schema fields are sent). */
export const putWorkflowDef = (def: WorkflowDef, signal?: AbortSignal) =>
  request<WorkflowDef>("PUT", `/workflows/${enc(def.id)}`, def, signal);

export const listActors = async (signal?: AbortSignal) =>
  (await request<ItemList<Actor>>("GET", "/actors", undefined, signal)).items;

/**
 * Recent runs of one workflow. `GET /runs` has no workflow filter, so the
 * newest `limit` runs are fetched and filtered here by `workflow_id`.
 */
export const listWorkflowRuns = async (workflowId: string, limit = 50, signal?: AbortSignal) =>
  (await request<ItemList<RunSummary>>("GET", `/runs?limit=${limit}`, undefined, signal)).items.filter(
    (r) => r.workflow_id === workflowId,
  );

export const getRun = (id: string, signal?: AbortSignal) =>
  request<RunDoc>("GET", `/runs/${enc(id)}`, undefined, signal);

/** `POST /runs`: start a run of a rule (a workflow runs through the rule that uses it). */
export const startRun = (ruleId: string, signal?: AbortSignal) =>
  request<RunDoc>("POST", "/runs", { rule_id: ruleId }, signal);

/** `GET /export?format=`: every live definition as `<kind>/<id>.<ext>` -> text. */
export const exportDefinitions = (format = "json", signal?: AbortSignal) =>
  request<ExportResult>("GET", `/export?format=${enc(format)}`, undefined, signal);

/** `POST /import`: a dry-run plan unless `apply` is true. */
export const importDefinitions = (
  files: Record<string, string>,
  apply: boolean,
  signal?: AbortSignal,
) => request<ImportPlan>("POST", "/import", { files, apply }, signal);

/** PLANNED `GET /repos` — see the module docstring. */
export const listRepos = async (signal?: AbortSignal) =>
  (await request<ItemList<Repo>>("GET", "/repos", undefined, signal)).items;
