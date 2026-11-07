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
 * as API data: the "Summarize document" workflow (v3) with its four steps on spark,
 * spark2 and thor, shaped like api/openapi.json answers it. The machines are
 * the Rules fixture's (spark is the engine node).
 */
export const SUMMARIZE_DOCUMENT_ID = "summarize-document";

export const SUMMARIZE_DOCUMENT: WorkflowDef = {
  id: SUMMARIZE_DOCUMENT_ID,
  name: "Summarize document",
  description: "Fetch a document, check it, have an agent summarize it, decide.",
  version: 3,
  inputs: [
    { name: "pr", type: "integer" },
    { name: "repo", type: "string" },
  ],
  variables: [],
  steps: [
    {
      id: "fetch-text",
      name: "Fetch text",
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
      id: "check-text",
      name: "Check text",
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
    { source: "inputs", source_port: "pr", target: "fetch-text", target_port: "pr" },
    { source: "inputs", source_port: "repo", target: "fetch-text", target_port: "repo" },
    { source: "inputs", source_port: "repo", target: "check-text", target_port: "repo" },
    { source: "fetch-text", source_port: "diff", target: "review", target_port: "diff" },
    { source: "review", source_port: "findings", target: "decide", target_port: "findings" },
    { source: "check-text", source_port: "passed", target: "decide", target_port: "passed" },
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
  { ...SUMMARIZE_DOCUMENT, ...({ deleted_at: null } as object) },
  BUILD_IMAGE,
];

export const ACTORS: Actor[] = [
  { id: "claude-summarizer", name: "Claude summarizer", kind: "agent", machine: "thor", harness: "claude", model: "opus" },
  { id: "ori", name: "Ori", kind: "human", machine: null },
];

/** A rule that runs Summarize document, so the Run button has something to start. */
export const SUMMARY_RULE_ID = "summarize-on-request";

export const WORKFLOW_RULES = RULES.map((r) =>
  r.id === SUMMARY_RULE_ID ? { ...r, workflow: { id: SUMMARIZE_DOCUMENT_ID } } : r,
);

export const RUN_ID = "run-7";

const MINUTE = 60_000;

export function workflowRunsFor(now: number): RunSummary[] {
  const at = (m: number) => new Date(now - m * MINUTE).toISOString();
  return [
    { id: RUN_ID, status: "failed", rule_id: SUMMARY_RULE_ID, workflow_id: SUMMARIZE_DOCUMENT_ID, started_by: "ori", created_at: at(5), finished_at: at(3) },
    { id: "run-6", status: "succeeded", rule_id: SUMMARY_RULE_ID, workflow_id: SUMMARIZE_DOCUMENT_ID, started_by: "trigger", created_at: at(70), finished_at: at(66) },
    { id: "run-5", status: "succeeded", rule_id: "build-and-publish", workflow_id: "build-image", started_by: "trigger", created_at: at(90), finished_at: at(88) },
  ];
}

/**
 * The persisted run state of run-7 (`GET /runs/run-7`): Fetch text, Run
 * tests and Review succeeded on their machines; Decide failed on spark.
 */
export const RUN_7: RunDoc = {
  id: RUN_ID,
  status: "failed",
  rule: { id: SUMMARY_RULE_ID },
  workflow: { id: SUMMARIZE_DOCUMENT_ID, version: 3 },
  created_at: "2026-10-03T11:55:00Z",
  finished_at: "2026-10-03T11:57:00Z",
  error: { step: "decide", message: "findings were empty" },
  steps: [
    { key: "fetch-text", def: "fetch-text", loop: null, status: "succeeded", attempt: 1, host: "spark", error: null },
    { key: "check-text", def: "check-text", loop: null, status: "succeeded", attempt: 1, host: "spark2", error: null },
    { key: "review", def: "review", loop: null, status: "succeeded", attempt: 1, host: "thor", error: null },
    { key: "decide", def: "decide", loop: null, status: "failed", attempt: 2, host: "spark", error: { message: "findings were empty" } },
    { key: "@action", def: "@action", loop: null, status: "skipped", attempt: 0, host: null, error: null },
  ],
};

/** A started run (`POST /runs`): pending everywhere. */
export const STARTED_RUN: RunDoc = {
  id: "run-8",
  status: "running",
  rule: { id: SUMMARY_RULE_ID },
  workflow: { id: SUMMARIZE_DOCUMENT_ID, version: 3 },
  created_at: "2026-10-03T12:00:00Z",
  finished_at: null,
  steps: [
    { key: "fetch-text", def: "fetch-text", status: "running", host: "spark" },
    { key: "check-text", def: "check-text", status: "pending", host: null },
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
    "workflows/summarize-document.json": JSON.stringify(SUMMARIZE_DOCUMENT, null, 2),
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

/**
 * `GET /workflows/summarize-document/describe` (d19): what culture_rules/model/describe.py
 * answers for SUMMARIZE_DOCUMENT — its steps in plain words, from the config only.
 */
export const SUMMARIZE_DOCUMENT_DESCRIBED = {
  id: SUMMARIZE_DOCUMENT_ID,
  kind: "workflow" as const,
  lines: [
    "1 fetch-text — code on spark",
    "2 check-text — code on spark2",
    "3 review — agent on thor",
    "4 decide — logic on spark",
  ],
  entries: [
    { label: "1", text: "code on spark", depth: 0, step: "fetch-text" },
    { label: "2", text: "code on spark2", depth: 0, step: "check-text" },
    { label: "3", text: "agent on thor", depth: 0, step: "review" },
    { label: "4", text: "logic on spark", depth: 0, step: "decide" },
  ],
};
