import type { Action, Condition, Rule } from "../../api/types";
import type { WorkflowDef } from "../../api/workflows";

/**
 * A self-contained document processing chain for the
 * Simple view's tests: three workflows chained by continuation rules, four
 * entry points on `prepare-document` (one disabled, one a continuation), a run-event
 * rule without a predecessor term ("from any workflow") and one action-only
 * rule (a D7 candidate).
 */
export const RUN_KEY = "document-chain:{trigger.data.repository}#{trigger.data.number}";

const comment = (name: string, body: string): Action => ({
  kind: "github.comment",
  name,
  only_at_chain_end: true,
  params: { actor: "github-app", body, number: "trigger.data.number", repo: "trigger.data.repository" },
});

export const ENDS_HERE = comment("Comment when the chain ends here", "Document chain built {{ workflow.outputs.commit_sha }}: not pushed.");
export const ON_FAILURE = comment("Hand back with the run link", "Document chain handed back ({{ run.error.code }})");
const REVIEW_END = comment("Comment the review", "Document chain review: {{ workflow.outputs.review }}");

const field = (name: string) => ({ field: name });
const eq = (left: string, right: unknown): Condition => ({
  op: "compare", cmp: "==", left: field(left), right: { literal: right },
});
const from = (workflowId: string): Condition => eq("data.workflow_id", workflowId);

const GUARD: Condition[] = [
  { op: "compare", cmp: "==", left: field("data.head_repo"), right: field("data.base_repo") },
  eq("data.draft", false),
  { op: "in", value: field("data.repository"), items: { var: "document_repos" } },
  { op: "compare", cmp: "!=", left: field("data.conclusion"), right: { literal: "success" } },
];

const pr = { number: "trigger.data.number", repo: "trigger.data.repository" };

/** Every prepare-document entry point holds these identically (D3-D6: shown once). */
const PREPARE_DOCUMENT_SHARED = {
  workflow: { id: "prepare-document", inputs: pr },
  action: ENDS_HERE,
  on_failure: ON_FAILURE,
  concurrency_key: RUN_KEY,
  max_attempts: 3,
  placement: { machine: "spark2" },
};

export const FOLD_RULES: Rule[] = [
  {
    id: "document-chain-checks",
    name: "Checks settled, not green",
    trigger: { kind: "event", params: { type: "github.pr.checks_settled" } },
    condition: { op: "and", args: GUARD },
    ...PREPARE_DOCUMENT_SHARED,
    counts_toward_budget: true,
    enabled: true,
  } as Rule,
  {
    id: "document-chain-comment",
    name: "Trusted PR comment",
    trigger: { kind: "event", params: { type: "github.comment.created" } },
    condition: eq("data.body", "/fix"),
    ...PREPARE_DOCUMENT_SHARED,
    counts_toward_budget: true,
    enabled: true,
  } as Rule,
  {
    id: "document-chain-review",
    name: "Trusted review",
    trigger: { kind: "event", params: { type: "github.review.submitted" } },
    ...PREPARE_DOCUMENT_SHARED,
    counts_toward_budget: true,
    enabled: false,
  } as Rule,
  {
    id: "document-chain-refix",
    name: "Review asked for changes",
    trigger: { kind: "event", params: { type: "rules.run.succeeded" } },
    condition: { op: "and", args: [from("validate-document"), eq("data.outputs.review", "request_changes")] },
    ...PREPARE_DOCUMENT_SHARED,
    counts_toward_budget: true,
    enabled: true,
  } as Rule,
  {
    id: "document-chain-validate-document",
    name: "Validate the document",
    trigger: { kind: "event", params: { type: "rules.run.succeeded" } },
    condition: {
      op: "and",
      args: [from("prepare-document"), { op: "in", value: field("data.outputs.verdict"), items: { literal: ["pass", "no_gate"] } }],
    },
    workflow: { id: "validate-document", inputs: { commit_sha: "trigger.data.outputs.commit_sha" } },
    action: REVIEW_END,
    on_failure: ON_FAILURE,
    concurrency_key: RUN_KEY,
    max_attempts: null,
    counts_toward_budget: false,
    placement: { machine: "spark2" },
    enabled: true,
  } as Rule,
  {
    id: "document-chain-publish",
    name: "Publish when approved",
    trigger: { kind: "event", params: { type: "rules.run.succeeded" } },
    condition: { op: "and", args: [from("validate-document"), eq("data.outputs.review", "approve")] },
    workflow: { id: "publish-document", inputs: pr },
    action: comment("Comment the push", "Pushed."),
    placement: { machine: "spark2" },
    enabled: true,
  },
  {
    id: "any-failure",
    name: "Any run failed",
    trigger: { kind: "event", params: { type: "rules.run.failed" } },
    workflow: { id: "publish-document", inputs: {} },
    action: { kind: "message", name: "Say it failed", params: { text: "trigger.data.error" } },
    enabled: true,
  },
  {
    id: "lone-alert",
    name: "Lone alert",
    trigger: { kind: "event", params: { type: "github.pr.closed" } },
    action: { kind: "message", name: "Say it", params: { text: "pushed" } },
    enabled: true,
  },
];

export const FOLD_WORKFLOWS: WorkflowDef[] = [
  {
    id: "prepare-document",
    name: "Prepare document",
    outputs: [{ name: "commit_sha" }, { name: "verdict" }],
    steps: [
      { id: "quiet", name: "Quiet period", kind: "logic" },
      { id: "fix", name: "Prepare the document", kind: "ai", placement: { actor: "document-agent" } },
      { id: "gate", name: "Validate content", kind: "code", placement: { machine: "spark2" } },
    ],
    edges: [],
  },
  { id: "validate-document", name: "Validate the document", outputs: [{ name: "review" }], steps: [], edges: [] },
  { id: "publish-document", name: "Publish the document", steps: [], edges: [] },
];
