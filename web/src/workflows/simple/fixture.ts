import type { Action, Condition, Rule } from "../../api/types";
import type { WorkflowDef } from "../../api/workflows";

/**
 * A small PR fixer, shaped like docs/rules/pr-fixer/rules/*.json, for the
 * Simple view's tests: three workflows chained by continuation rules, four
 * entry points on `pr-fix` (one disabled, one a continuation), a run-event
 * rule without a predecessor term ("from any workflow") and one action-only
 * rule (a D7 candidate).
 */
export const RUN_KEY = "pr-fixer:{trigger.data.repository}#{trigger.data.number}";

const comment = (name: string, body: string): Action => ({
  kind: "github.comment",
  name,
  only_at_chain_end: true,
  params: { actor: "github-app", body, number: "trigger.data.number", repo: "trigger.data.repository" },
});

export const ENDS_HERE = comment("Comment when the chain ends here", "PR fixer built {{ workflow.outputs.commit_sha }}: not pushed.");
export const ON_FAILURE = comment("Hand back with the run link", "PR fixer handed back ({{ run.error.code }})");
const REVIEW_END = comment("Comment the review", "PR fixer review: {{ workflow.outputs.review }}");

const field = (name: string) => ({ field: name });
const eq = (left: string, right: unknown): Condition => ({
  op: "compare", cmp: "==", left: field(left), right: { literal: right },
});
const from = (workflowId: string): Condition => eq("data.workflow_id", workflowId);

const GUARD: Condition[] = [
  { op: "compare", cmp: "==", left: field("data.head_repo"), right: field("data.base_repo") },
  eq("data.draft", false),
  { op: "in", value: field("data.repository"), items: { var: "fixer_repos" } },
  { op: "compare", cmp: "!=", left: field("data.conclusion"), right: { literal: "success" } },
];

const pr = { number: "trigger.data.number", repo: "trigger.data.repository" };

/** Every pr-fix entry point holds these identically (D3-D6: shown once). */
const PR_FIX_SHARED = {
  workflow: { id: "pr-fix", inputs: pr },
  action: ENDS_HERE,
  on_failure: ON_FAILURE,
  concurrency_key: RUN_KEY,
  max_attempts: 3,
  placement: { machine: "spark2" },
};

export const FOLD_RULES: Rule[] = [
  {
    id: "pr-fixer-checks",
    name: "Checks settled, not green",
    trigger: { kind: "event", params: { type: "github.pr.checks_settled" } },
    condition: { op: "and", args: GUARD },
    ...PR_FIX_SHARED,
    counts_toward_budget: true,
    enabled: true,
  } as Rule,
  {
    id: "pr-fixer-comment",
    name: "Trusted PR comment",
    trigger: { kind: "event", params: { type: "github.comment.created" } },
    condition: eq("data.body", "/fix"),
    ...PR_FIX_SHARED,
    counts_toward_budget: true,
    enabled: true,
  } as Rule,
  {
    id: "pr-fixer-review",
    name: "Trusted review",
    trigger: { kind: "event", params: { type: "github.review.submitted" } },
    ...PR_FIX_SHARED,
    counts_toward_budget: true,
    enabled: false,
  } as Rule,
  {
    id: "pr-fixer-refix",
    name: "Review asked for changes",
    trigger: { kind: "event", params: { type: "rules.run.succeeded" } },
    condition: { op: "and", args: [from("review-commit"), eq("data.outputs.review", "request_changes")] },
    ...PR_FIX_SHARED,
    counts_toward_budget: true,
    enabled: true,
  } as Rule,
  {
    id: "pr-fixer-review-commit",
    name: "Review the fix",
    trigger: { kind: "event", params: { type: "rules.run.succeeded" } },
    condition: {
      op: "and",
      args: [from("pr-fix"), { op: "in", value: field("data.outputs.verdict"), items: { literal: ["pass", "no_gate"] } }],
    },
    workflow: { id: "review-commit", inputs: { commit_sha: "trigger.data.outputs.commit_sha" } },
    action: REVIEW_END,
    on_failure: ON_FAILURE,
    concurrency_key: RUN_KEY,
    max_attempts: null,
    counts_toward_budget: false,
    placement: { machine: "spark2" },
    enabled: true,
  } as Rule,
  {
    id: "pr-fixer-publish",
    name: "Publish when approved",
    trigger: { kind: "event", params: { type: "rules.run.succeeded" } },
    condition: { op: "and", args: [from("review-commit"), eq("data.outputs.review", "approve")] },
    workflow: { id: "publish-fix", inputs: pr },
    action: comment("Comment the push", "Pushed."),
    placement: { machine: "spark2" },
    enabled: true,
  },
  {
    id: "any-failure",
    name: "Any run failed",
    trigger: { kind: "event", params: { type: "rules.run.failed" } },
    workflow: { id: "publish-fix", inputs: {} },
    action: { kind: "message", name: "Say it failed", params: { text: "trigger.data.error" } },
    enabled: true,
  },
  {
    id: "lone-alert",
    name: "Lone alert",
    trigger: { kind: "event", params: { type: "github.push" } },
    action: { kind: "message", name: "Say it", params: { text: "pushed" } },
    enabled: true,
  },
];

export const FOLD_WORKFLOWS: WorkflowDef[] = [
  {
    id: "pr-fix",
    name: "PR fix",
    outputs: [{ name: "commit_sha" }, { name: "verdict" }],
    steps: [
      { id: "quiet", name: "Quiet period", kind: "logic" },
      { id: "fix", name: "Fix the PR", kind: "ai", placement: { actor: "qwen-fixer" } },
      { id: "gate", name: "Test gate", kind: "code", placement: { machine: "spark2" } },
    ],
    edges: [],
  },
  { id: "review-commit", name: "Review the fix", outputs: [{ name: "review" }], steps: [], edges: [] },
  { id: "publish-fix", name: "Publish the fix", steps: [], edges: [] },
];
