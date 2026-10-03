/**
 * Hand-maintained types for the culture-rules HTTP API, against the
 * committed api/openapi.json (repo root) and schemas/*.json. The API serves
 * definition bodies as free-form objects (`additionalProperties: true`), so
 * the shapes below follow the JSON Schemas the server validates against
 * (rule.schema.json, machine.schema.json, ...). Only the fields this UI
 * reads are typed; everything else rides along untouched.
 *
 * When api/openapi.json changes (obligation o10 regenerates it in the same
 * commit as any route change), update this file in the same PR.
 */

/** Every list route answers `{"items": [...]}` (ItemList). */
export interface ItemList<T> {
  items: T[];
}

/** The error envelope every failure carries (ErrorEnvelope). */
export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    errors: { path: string; code: string; message: string }[];
  };
}

export type Role = "viewer" | "editor" | "admin";

/**
 * `GET /whoami` — the WhoAmI schema in api/openapi.json. `kind` says which
 * credential carried it: Cloudflare Access SSO (browser), a service token,
 * or an agent. `roles` is a subset of viewer < editor < admin.
 */
export interface Whoami {
  identity: string;
  kind: "sso" | "service" | "agent";
  roles: string[];
}

export interface Trigger {
  kind: string;
  params?: Record<string, unknown>;
}

export interface WorkflowRef {
  id: string;
  version?: number | null;
  /** workflow input name -> reference (e.g. trigger.data.sha) */
  inputs?: Record<string, string>;
}

export interface Action {
  kind: string;
  name?: string;
  params?: Record<string, unknown>;
}

export interface Placement {
  machine?: string | null;
  actor?: string | null;
  requirement?: string[] | null;
}

/** Condition tree (culture_rules/model/condition.py): every node has an `op`. */
export type Operand = { field: string } | { var: string } | { literal: unknown };
export type Condition =
  | { op: "compare"; cmp: string; left: Operand; right: Operand }
  | { op: "and" | "or"; args: Condition[] }
  | { op: "not"; arg: Condition }
  | { op: "exists"; arg: Operand }
  | { op: "in"; value: Operand; items: Operand }
  | { op: "matches"; value: Operand; pattern: string };

export interface Rule {
  id: string;
  name: string;
  description?: string;
  trigger: Trigger;
  condition?: Condition | null;
  workflow?: WorkflowRef | null;
  action: Action;
  placement?: Placement | null;
  must_after?: string[];
  may_after?: string[];
  /** Rules this one replaces (the `supersedes` relationship). */
  supersedes?: string[];
  enabled?: boolean;
}

export interface Workflow {
  id: string;
  name: string;
}

export interface Machine {
  name: string;
  platform?: string;
  capabilities?: string[];
  roles?: string[];
  enabled?: boolean;
}

/** `GET /runs` items (server/app.py `_run_summary`). */
export interface RunSummary {
  id: string;
  status: "pending" | "running" | "waiting" | "succeeded" | "failed" | "cancelled" | string;
  rule_id: string | null;
  workflow_id: string | null;
  started_by: string | null;
  created_at: string | null;
  finished_at: string | null;
  /** The machines this run's steps were dispatched to (sorted, distinct). */
  hosts?: string[];
}

/**
 * `GET /rules/{id}/history` items, newest first: a run of the rule
 * (`kind: "run"`, a RunSummary) or a recorded skip (`kind: "decision"`,
 * culture_rules/engine/decisions.py: superseded_by, blocked_by_predecessor,
 * group_lost, with `by` naming the responsible rules).
 */
export type RuleHistoryItem =
  | ({ kind: "run"; at: string | null } & RunSummary)
  | {
      kind: "decision";
      at: string | null;
      rule_id: string;
      event_id: string;
      reason: "superseded_by" | "blocked_by_predecessor" | "group_lost" | string;
      by: string[];
      message?: string;
      host?: string;
    };

/** `GET /asks` items: a human ask (`asks` collection, culture_rules/actors/human.py). */
export interface Ask {
  id: string;
  run_id: string;
  step_id?: string;
  question: string;
  options: string[] | null;
  deadline?: string;
  status?: "open" | "answered" | "expired" | string;
  asked_at?: string;
}

/**
 * `GET /machines/status` items: one enrolled machine's live state, from its
 * heartbeat and the active runs.
 */
export interface MachineStatus {
  name: string;
  online: boolean;
  /** Last heartbeat, ISO-8601; null when the machine never reported. */
  last_seen?: string | null;
  /** Percent 0-100 per resource; null = not reported (a CPU-only host has no GPU). */
  load: { cpu: number | null; gpu: number | null; mem: number | null } | null;
  running: { step: string; workflow: string; run_id?: string }[];
  queue_depth: number;
}

/** `GET /repos` items: a definitions repository the server is configured with. */
export interface Repo {
  name: string;
  /** The configured location: a local path or a git remote. */
  url?: string;
  /** A local working tree: exports can commit to it. */
  writable?: boolean;
}

export interface ImportChange {
  kind: string;
  id: string;
  path: string;
  action: string;
}

/** `POST /import`: the plan (applied only when the request said `apply`). */
export interface ImportPlan {
  applied: boolean;
  changes: ImportChange[];
  errors?: { path: string; code: string; message: string }[];
}

/** `GET /export`: every live definition as `<kind>/<id>.<ext>` -> text. */
export interface ExportResult {
  format: string;
  files: Record<string, string>;
}

/** `POST /export`: an export into a configured repository (RepoExportResult). */
export interface RepoExportResult {
  repo: string;
  applied: boolean;
  committed: boolean;
  pushed: boolean;
  commit: string | null;
  changes: ImportChange[];
}
