import type { Actor } from "../api/actors";

/**
 * The 'Chosen — Actors' board (design canvas row 'Chosen', D-Actors) as API
 * data: the same eight actors, shaped like the culture-rules API answers
 * them (schemas/actor.schema.json inside `{"items": [...]}`). Machines are
 * the rules fixture's three (spark teal, thor amber, spark2 blue).
 */
export const SELECTED_ACTOR_ID = "claude-code";

export const ACTORS: Actor[] = [
  {
    id: SELECTED_ACTOR_ID,
    name: "Claude Code",
    kind: "agent",
    machine: "spark",
    config_source: "repo",
    repo: "culture-rules",
    harness: "claude",
    model: "opus 4.6",
    capabilities: ["review", "write code", "triage"],
    enabled: true,
  },
  { id: "codex", name: "Codex", kind: "agent", machine: "thor", config_source: "db", harness: "codex", model: "gpt-5", capabilities: ["write code"], enabled: true },
  { id: "colleague", name: "Colleague", kind: "agent", machine: "spark2", config_source: "db", harness: "colleague", capabilities: ["review", "explore"], enabled: true },
  { id: "pr-watcher", name: "PR Watcher", kind: "daemon", machine: "spark", config_source: "db", capabilities: ["watch"], enabled: true },
  { id: "github", name: "GitHub", kind: "service", config_source: "db", capabilities: ["comment"], enabled: true },
  { id: "ori", name: "Ori", kind: "human", config_source: "db", capabilities: ["approve"], enabled: true },
  { id: "thor-runner", name: "thor runner", kind: "runner", machine: "thor", config_source: "db", capabilities: ["run code"], enabled: true },
  { id: "reachy", name: "Reachy", kind: "robot", config_source: "db", capabilities: ["speak"], enabled: false },
];

/*
 * Model actors (t10): the `model_gateways` variable, an enrolment plan and
 * the references of the selected actor, shaped like api/openapi.json's
 * EnrolModelsResult / ActorReferences. URLs are loopback only
 * (scripts/scan-secrets.py rejects any other endpoint in a committed file).
 */

/** `GET /variables/model_gateways`: the d4 list of "machine=URL" strings. */
export const MODEL_GATEWAYS_VARIABLE = {
  id: "model_gateways",
  name: "model_gateways",
  value: ["spark=http://127.0.0.1:4000", "spark2=http://127.0.0.1:4001"],
  version: 1,
  updated_by: "ori",
  updated_at: "2026-10-19T08:00:00Z",
};

const endpoint = (model: string, machine: string, url: string, pool = model) => ({
  id: model,
  name: model,
  description: `Model ${model} served on ${machine}`,
  kind: "agent",
  machine,
  params: {
    base_url: url,
    model,
    concurrency_pool: pool,
    max_concurrency: 1,
    api_key: "grant:LOBES_GATEWAY_API_KEY",
  },
  enabled: true,
  config_source: "db",
});

/** `POST /actors/enrol-models` with `dry_run: true` and `match: ["qwen*"]`. */
export const ENROL_PLAN = {
  dry_run: true,
  gateways: [
    { machine: "spark", url: "http://127.0.0.1:4000", models: ["qwen3-coder", "qwen3-8b", "llama-3.3"] },
    { machine: "spark2", url: "http://127.0.0.1:4001", models: ["qwen3-coder", "qwen3-vl"] },
  ],
  changes: [
    {
      action: "create",
      id: "qwen3-coder",
      reason: "served by spark",
      body: endpoint("qwen3-coder", "spark", "http://127.0.0.1:4000", "coder"),
    },
    {
      action: "create",
      id: "qwen3-vl",
      reason: "served by spark2",
      body: endpoint("qwen3-vl", "spark2", "http://127.0.0.1:4001"),
    },
    { action: "unchanged", id: "qwen3-8b", reason: "already enrolled", body: null },
    { action: "refused", id: "qwen3-max", reason: "id held by a human actor", body: null },
  ],
  also_served_by: { "qwen3-coder": ["spark2"] },
  unseen: ["qwen2.5-old"],
  applied: [],
  failed: [],
};

/** The same request applied (`dry_run: false`): one created, one refused by the store. */
export const ENROL_APPLIED = {
  ...ENROL_PLAN,
  dry_run: false,
  applied: [{ id: "qwen3-coder", action: "create" }],
  failed: [
    {
      id: "qwen3-vl",
      action: "create",
      code: "model_taken",
      message: "model_taken: qwen3-vl is held by vision; disable vision first",
    },
  ],
};

/** `GET /actors/claude-code/references`: two steps of one workflow, and a rule's placement. */
export const REFERENCES = {
  actor_id: SELECTED_ACTOR_ID,
  items: [
    { kind: "workflow", id: "newspaper", path: "summarise", via: "action", enabled: true },
    { kind: "workflow", id: "newspaper", path: "edition.body[write]", via: "action", enabled: true },
    { kind: "workflow", id: "review-pr", path: "review", via: "action", enabled: true },
    { kind: "rule", id: "nightly-news", path: "placement.actor", via: "placement", enabled: true },
  ],
};

/** `GET /workflows/{id}`: just what the Move form reads (the revision). */
export const REFERENCED_WORKFLOWS: Record<string, { id: string; name: string; revision: number; steps: unknown[] }> = {
  newspaper: { id: "newspaper", name: "Daily newspaper", revision: 4, steps: [] },
  "review-pr": { id: "review-pr", name: "Review PR", revision: 9, steps: [] },
};

/** `POST /actors/claude-code/disable` without force: 409 actor_in_use naming its referrers. */
export const ACTOR_IN_USE = {
  error: {
    code: "actor_in_use",
    message:
      "actor_in_use: claude-code is used by workflows/newspaper, workflows/review-pr, rules/nightly-news; repoint or disable them, or pass force",
    errors: [
      { path: "workflows/newspaper", code: "actor_in_use", message: "workflow newspaper uses claude-code (action at summarise)" },
      { path: "workflows/review-pr", code: "actor_in_use", message: "workflow review-pr uses claude-code (action at review)" },
      { path: "rules/nightly-news", code: "actor_in_use", message: "rule nightly-news uses claude-code (placement at placement.actor)" },
    ],
  },
};

/** `POST /workflows/{id}/repoint-actor` refused: the revision sent is stale. */
export const REVISION_CONFLICT = {
  error: {
    code: "revision_conflict",
    message: "workflows/newspaper is at revision 5, not 4",
    errors: [{ path: "expected_revision", code: "revision_conflict", message: "current_revision 5" }],
  },
};

/** What an older server (no model-actor routes) answers: FastAPI's generic 404. */
export const GENERIC_NOT_FOUND = { detail: "Not Found" };
