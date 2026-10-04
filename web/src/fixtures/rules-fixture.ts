import type { Actor } from "../api/actors";
import type { Machine, Rule, RunSummary, Whoami, Workflow } from "../api/types";

/**
 * The 'Chosen — Rules' board (design canvas row 'Chosen', D-Rules) as API
 * data: the same five rules, three machines and four recent runs, shaped
 * like the culture-rules HTTP API answers them (api/openapi.json — every
 * list is `{"items": [...]}`; definition bodies follow schemas/*.json).
 */
export const WHOAMI: Whoami = {
  identity: "ori",
  kind: "sso",
  roles: ["viewer", "editor", "admin"],
};

export const MACHINES: Machine[] = [
  { name: "spark", platform: "linux-aarch64", capabilities: [], roles: ["engine_node"], enabled: true },
  { name: "thor", platform: "linux-aarch64", capabilities: ["gpu"], roles: ["runner"], enabled: true },
  { name: "spark2", platform: "linux-aarch64", capabilities: [], roles: ["runner"], enabled: true },
];

/** Actors the trigger picker reads: enabled apps declare events, runners register commands. */
export const ACTORS: Actor[] = [
  {
    id: "github-app",
    name: "GitHub",
    kind: "app",
    enabled: true,
    params: {
      surface: "github",
      events: ["github.pr.opened", "github.push"],
      actions: ["github.comment"],
      connection: {},
    },
  },
  {
    id: "discord-app",
    name: "Discord",
    kind: "app",
    enabled: false,
    params: {
      surface: "discord",
      events: ["discord.message.created"],
      actions: ["jira.comment"],
      connection: {},
    },
  },
  {
    id: "ci-runner",
    name: "CI runner",
    kind: "runner",
    enabled: true,
    params: {
      commands: {
        "disk-free": { argv: ["df", "-h"], params: { path: "string" } },
        "gpu-temp": { argv: ["nvidia-smi"] },
      },
    },
  },
];

export const WORKFLOWS: Workflow[] = [
  { id: "build-image", name: "Build image", outputs: [{ name: "image" }, { name: "digest" }] },
  { id: "review-pr", name: "Review PR" },
];

export const SELECTED_RULE_ID = "build-and-publish";

export const RULES: Rule[] = [
  {
    id: "review-on-approve",
    name: "Review on approve",
    trigger: { kind: "event", params: { label: "PR approved" } },
    action: { kind: "mesh.message", name: "Announce" },
    placement: { machine: "spark" },
    enabled: true,
  },
  {
    id: "clean-caches",
    name: "Clean caches",
    trigger: { kind: "schedule", params: { label: "Every night" } },
    action: { kind: "code.run", name: "Clean" },
    placement: { machine: "spark" },
    enabled: false,
  },
  {
    id: SELECTED_RULE_ID,
    name: "Build and publish",
    trigger: { kind: "event", params: { label: "Push to main" } },
    condition: {
      op: "compare",
      cmp: "==",
      left: { var: "verdict" },
      right: { literal: "approve" },
    },
    workflow: {
      id: "build-image",
      inputs: { commit: "trigger.data.sha", repo: "trigger.data.repo" },
    },
    action: { kind: "http.call", name: "Publish", params: { tag: "workflow.outputs.image" } },
    placement: { machine: "thor" },
    must_after: ["review-on-approve"],
    enabled: true,
  },
  {
    id: "train-batch",
    name: "Train batch",
    trigger: { kind: "schedule", params: { label: "Hourly" } },
    action: { kind: "code.run", name: "Train" },
    placement: { machine: "thor" },
    enabled: true,
  },
  {
    id: "triage-bugs",
    name: "Triage bugs",
    trigger: { kind: "event", params: { label: "Issue opened" } },
    action: { kind: "github.comment", name: "Label" },
    placement: { machine: "spark2" },
    enabled: true,
  },
];

const MINUTE = 60_000;

/** The board's 'Last runs' column — 4m, 1h, 3h (failed), yesterday — relative to `now`. */
export function runsFor(now: number): RunSummary[] {
  const at = (minutesAgo: number) => new Date(now - minutesAgo * MINUTE).toISOString();
  return [
    { id: "run-4", status: "succeeded", rule_id: SELECTED_RULE_ID, workflow_id: "build-image", started_by: "trigger", created_at: at(4), finished_at: at(2) },
    { id: "run-3", status: "succeeded", rule_id: SELECTED_RULE_ID, workflow_id: "build-image", started_by: "trigger", created_at: at(62), finished_at: at(60) },
    { id: "run-2", status: "failed", rule_id: SELECTED_RULE_ID, workflow_id: "build-image", started_by: "trigger", created_at: at(185), finished_at: at(183) },
    { id: "run-1", status: "succeeded", rule_id: SELECTED_RULE_ID, workflow_id: "build-image", started_by: "trigger", created_at: at(26 * 60), finished_at: at(26 * 60 - 2) },
  ];
}
