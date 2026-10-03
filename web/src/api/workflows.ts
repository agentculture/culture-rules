/**
 * Workflows-tab API calls and types, built on client.ts's shared `request`,
 * `getJson` and `items` helpers. Shapes follow the committed
 * api/openapi.json and schemas/workflow.schema.json; run documents follow
 * the engine's persisted run state (culture_rules/engine/runs.py
 * `_new_state`). Every call here is a route in api/openapi.json.
 */
import { getJson, items, listRuns, request } from "./client";
import type { ExportResult, ImportPlan, Placement, Repo, RepoExportResult } from "./types";

export type { ExportResult, ImportChange, ImportPlan, Repo, RepoExportResult } from "./types";

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

const enc = encodeURIComponent;

export const listWorkflowDefs = (signal?: AbortSignal) => items<WorkflowDef>("/workflows", signal);

export const getWorkflowDef = (id: string, signal?: AbortSignal) =>
  getJson<WorkflowDef>(`/workflows/${enc(id)}`, signal);

/** `PUT /workflows/{id}`: replace the definition (only schema fields are sent). */
export const putWorkflowDef = (def: WorkflowDef, signal?: AbortSignal) =>
  request<WorkflowDef>("PUT", `/workflows/${enc(def.id)}`, def, signal);

/** `POST /workflows`: create a definition; a 409 means the id is taken (deleted ones included). */
export const createWorkflowDef = (def: WorkflowDef, signal?: AbortSignal) =>
  request<WorkflowDef>("POST", "/workflows", def, signal);

/** `POST /workflows/{id}/enable|disable`: answers the stored definition. */
export const setWorkflowEnabled = (id: string, enabled: boolean, signal?: AbortSignal) =>
  request<WorkflowDef>("POST", `/workflows/${enc(id)}/${enabled ? "enable" : "disable"}`, undefined, signal);

/** `DELETE /workflows/{id}`: a soft delete (409 while a rule still uses it); `restoreWorkflowDef` undoes it. */
export const deleteWorkflowDef = (id: string, signal?: AbortSignal) =>
  request<unknown>("DELETE", `/workflows/${enc(id)}`, undefined, signal);

/** `POST /workflows/{id}/restore`: bring a soft-deleted definition back. */
export const restoreWorkflowDef = (id: string, signal?: AbortSignal) =>
  request<WorkflowDef>("POST", `/workflows/${enc(id)}/restore`, undefined, signal);

export const listActors = (signal?: AbortSignal) => items<Actor>("/actors", signal);

/** `GET /runs?workflow_id=`: the most recent runs of one workflow. */
export const listWorkflowRuns = async (workflowId: string, limit = 50, signal?: AbortSignal) =>
  // The server filters; the client re-checks so a stale server cannot mix workflows.
  (await listRuns({ workflow_id: workflowId, limit }, signal)).filter((r) => r.workflow_id === workflowId);

export const getRun = (id: string, signal?: AbortSignal) =>
  getJson<RunDoc>(`/runs/${enc(id)}`, signal);

/** `POST /runs`: start a run of a rule (a workflow runs through the rule that uses it). */
export const startRun = (ruleId: string, signal?: AbortSignal) =>
  request<RunDoc>("POST", "/runs", { rule_id: ruleId }, signal);

/** `GET /export?format=`: every live definition as `<kind>/<id>.<ext>` -> text. */
export const exportDefinitions = (format = "json", signal?: AbortSignal) =>
  getJson<ExportResult>(`/export?format=${enc(format)}`, signal);

/** `POST /import`: a dry-run plan unless `apply` is true. */
export const importDefinitions = (
  files: Record<string, string>,
  apply: boolean,
  signal?: AbortSignal,
) => request<ImportPlan>("POST", "/import", { files, apply }, signal);

/** `GET /repos`: the definition repositories the server is configured with. */
export const listRepos = (signal?: AbortSignal) => items<Repo>("/repos", signal);

/** `POST /export` into a configured repository: a dry-run plan unless `apply` (then a commit). */
export const exportToRepo = (repo: string, apply: boolean, signal?: AbortSignal) =>
  request<RepoExportResult>("POST", "/export", { repo, apply }, signal);

/** `POST /import` from a configured repository: a dry-run plan unless `apply`. */
export const importFromRepo = (repo: string, apply: boolean, signal?: AbortSignal) =>
  request<ImportPlan>("POST", "/import", { repo, apply }, signal);
