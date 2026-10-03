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
