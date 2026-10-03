import type { RunSummary } from "../api/types";
import type {
  Actor,
  ExportResult,
  ImportPlan,
  Repo,
  RunDoc,
  WorkflowDef,
} from "../api/workflows";
import { RULES } from "../fixtures/rules-fixture";

/**
 * The 'Chosen — Workflows' board (design canvas row 'Chosen', D-Workflows)
 * as API data: the "Review PR" workflow (v3) with its four steps on spark,
 * spark2 and thor, shaped like api/openapi.json answers it. The machines are
 * the Rules fixture's (spark is the engine node).
 */
export const REVIEW_PR_ID = "review-pr";

export const REVIEW_PR: WorkflowDef = {
  id: REVIEW_PR_ID,
  name: "Review PR",
  description: "Fetch a PR's diff, test it, have an agent review it, decide.",
  version: 3,
  inputs: [
    { name: "pr", type: "integer" },
    { name: "repo", type: "string" },
  ],
  variables: [],
  steps: [
    {
      id: "fetch-diff",
      name: "Fetch diff",
      kind: "code",
      inputs: [
        { name: "pr", type: "integer" },
        { name: "repo", type: "string" },
      ],
      outputs: [{ name: "diff", type: "string" }],
      placement: { machine: "spark" },
      enabled: true,
    },
    {
      id: "run-tests",
      name: "Run tests",
      kind: "code",
      inputs: [{ name: "repo", type: "string" }],
      outputs: [{ name: "passed", type: "boolean" }],
      placement: { machine: "spark2" },
      enabled: true,
    },
    {
      id: "review",
      name: "Review",
      kind: "ai",
      inputs: [{ name: "diff", type: "string" }],
      outputs: [
        { name: "findings", type: "array" },
        { name: "owner", type: "string" },
      ],
      placement: { machine: "thor" },
      config: { harness: "claude", model: "opus" },
      enabled: true,
    },
    {
      id: "decide",
      name: "Decide",
      kind: "logic",
      inputs: [
        { name: "findings", type: "array" },
        { name: "passed", type: "boolean" },
      ],
      outputs: [{ name: "verdict", type: "string" }],
      placement: { machine: "spark" },
      enabled: true,
    },
  ],
  edges: [
    { source: "inputs", source_port: "pr", target: "fetch-diff", target_port: "pr" },
    { source: "inputs", source_port: "repo", target: "fetch-diff", target_port: "repo" },
    { source: "inputs", source_port: "repo", target: "run-tests", target_port: "repo" },
    { source: "fetch-diff", source_port: "diff", target: "review", target_port: "diff" },
    { source: "review", source_port: "findings", target: "decide", target_port: "findings" },
    { source: "run-tests", source_port: "passed", target: "decide", target_port: "passed" },
  ],
  outputs: [
    { name: "verdict", type: "string", source: "steps.decide.outputs.verdict" },
    { name: "owner", type: "string", source: "steps.review.outputs.owner" },
  ],
  enabled: true,
  schema_version: "1.0",
};

export const BUILD_IMAGE: WorkflowDef = {
  id: "build-image",
  name: "Build image",
  version: 1,
  inputs: [
    { name: "commit", type: "string" },
    { name: "repo", type: "string" },
  ],
  steps: [
    {
      id: "build",
      name: "Build",
      kind: "code",
      inputs: [{ name: "commit", type: "string" }],
      outputs: [{ name: "image", type: "string" }],
      placement: { requirement: ["gpu"] },
      enabled: true,
    },
  ],
  edges: [{ source: "inputs", source_port: "commit", target: "build", target_port: "commit" }],
  outputs: [{ name: "image", type: "string", source: "steps.build.outputs.image" }],
  enabled: true,
};

/** Store documents carry bookkeeping beyond the schema; the editor must not send it back. */
export const WORKFLOW_DOCS: WorkflowDef[] = [
  { ...REVIEW_PR, ...({ deleted_at: null } as object) },
  BUILD_IMAGE,
];

export const ACTORS: Actor[] = [
  { id: "claude-reviewer", name: "Claude reviewer", kind: "agent", machine: "thor", harness: "claude", model: "opus" },
  { id: "ori", name: "Ori", kind: "human", machine: null },
];

/** A rule that runs Review PR, so the Run button has something to start. */
export const REVIEW_RULE_ID = "review-on-approve";

export const WORKFLOW_RULES = RULES.map((r) =>
  r.id === REVIEW_RULE_ID ? { ...r, workflow: { id: REVIEW_PR_ID } } : r,
);

export const RUN_ID = "run-7";

const MINUTE = 60_000;

export function workflowRunsFor(now: number): RunSummary[] {
  const at = (m: number) => new Date(now - m * MINUTE).toISOString();
  return [
    { id: RUN_ID, status: "failed", rule_id: REVIEW_RULE_ID, workflow_id: REVIEW_PR_ID, started_by: "ori", created_at: at(5), finished_at: at(3) },
    { id: "run-6", status: "succeeded", rule_id: REVIEW_RULE_ID, workflow_id: REVIEW_PR_ID, started_by: "trigger", created_at: at(70), finished_at: at(66) },
    { id: "run-5", status: "succeeded", rule_id: "build-and-publish", workflow_id: "build-image", started_by: "trigger", created_at: at(90), finished_at: at(88) },
  ];
}

/**
 * The persisted run state of run-7 (`GET /runs/run-7`): Fetch diff, Run
 * tests and Review succeeded on their machines; Decide failed on spark.
 */
export const RUN_7: RunDoc = {
  id: RUN_ID,
  status: "failed",
  rule: { id: REVIEW_RULE_ID },
  workflow: { id: REVIEW_PR_ID, version: 3 },
  created_at: "2026-10-03T11:55:00Z",
  finished_at: "2026-10-03T11:57:00Z",
  error: { step: "decide", message: "findings were empty" },
  steps: [
    { key: "fetch-diff", def: "fetch-diff", loop: null, status: "succeeded", attempt: 1, host: "spark", error: null },
    { key: "run-tests", def: "run-tests", loop: null, status: "succeeded", attempt: 1, host: "spark2", error: null },
    { key: "review", def: "review", loop: null, status: "succeeded", attempt: 1, host: "thor", error: null },
    { key: "decide", def: "decide", loop: null, status: "failed", attempt: 2, host: "spark", error: { message: "findings were empty" } },
    { key: "@action", def: "@action", loop: null, status: "skipped", attempt: 0, host: null, error: null },
  ],
};

/** A started run (`POST /runs`): pending everywhere. */
export const STARTED_RUN: RunDoc = {
  id: "run-8",
  status: "running",
  rule: { id: REVIEW_RULE_ID },
  workflow: { id: REVIEW_PR_ID, version: 3 },
  created_at: "2026-10-03T12:00:00Z",
  finished_at: null,
  steps: [
    { key: "fetch-diff", def: "fetch-diff", status: "running", host: "spark" },
    { key: "run-tests", def: "run-tests", status: "pending", host: null },
    { key: "review", def: "review", status: "pending", host: null },
    { key: "decide", def: "decide", status: "pending", host: null },
  ],
};

export const REPOS: Repo[] = [
  { name: "agentculture/workflows" },
  { name: "agentculture/rules-lab" },
];

export const EXPORT_RESULT: ExportResult = {
  format: "json",
  files: {
    "workflows/review-pr.json": JSON.stringify(REVIEW_PR, null, 2),
    "workflows/build-image.json": JSON.stringify(BUILD_IMAGE, null, 2),
  },
};

export function importPlan(apply: boolean): ImportPlan {
  return {
    applied: apply,
    changes: [{ kind: "workflows", id: "triage", path: "workflows/triage.json", action: "create" }],
    errors: [],
  };
}

/** A workflow file a user imports. */
export const IMPORT_FILE_NAME = "triage.json";
export const IMPORT_FILE_TEXT = JSON.stringify({
  id: "triage",
  name: "Triage",
  steps: [{ id: "label", kind: "logic" }],
});
