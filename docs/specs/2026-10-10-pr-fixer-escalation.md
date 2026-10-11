# PR fixer escalation

> PR fixer escalation: within one story, a try that does not pass is retried by a stronger fixer (Qwen, then Codex), each try reviewed by a different model, and an unavailable level falls through instead of stalling

## Audience

- Authors of PRs in vars.`fixer_repos` whose PR the fixer works on, and the operator who watches the fixer's stories

## Before → After

- Before: Every try of a story uses qwen-fixer on spark2 and every review uses codex-reviewer; a PR Qwen cannot fix burns all 3 tries on the same model and hands back
- After: Try 1 is fixed by qwen-fixer and reviewed by codex-reviewer; try 2 by codex-fixer (Codex on spark, its commit carried to spark2's gate) and reviewed by qwen-reviewer; try 3 by the next enabled level; the status comment names the level and the fixer of each try

## Why it matters

- A second model often fixes what the first could not; escalating within the 3-try budget raises the fix rate without more tries or more cost on easy PRs

## Requirements

- A shared variable `fixer_levels` lists the levels in order, each {level, actor, reviewer, pool, queue, enabled}, plus `fixer_unavailable` (next or `hand_back`); it can only choose among agent and review steps that exist in the trusted workflows
  - honesty: Changing `fixer_levels` cannot run an actor or workflow that is not in the trusted pr-fix and review-commit; a level naming an unknown step fails closed
- queue.progress resolves a request's level at dispatch from its try (the key's count plus one), skipping a level that is unavailable (actor disabled or deleted, its pool over budget, or a `capacity_exhausted` cooldown) as `fixer_unavailable` says, and writes fixer, reviewer, level and attempt into the dispatch event; each level has its own queue and pool (pr-fixer on qwen-spark2, pr-fixer-codex on codex-spark), and queue.stop covers every queue
  - honesty: Each level's queue is first come, first served on its own pool, so a busy Qwen never delays a Codex try; /stop removes a story's request from every queue
- pr-fix stays one workflow: one agent step per level guarded by `when fixer == <actor>`, a Codex hand-off that exports the fixer's commits (start..head, size-capped) from spark into the store and imports them into a fixer-owned worktree on spark2, then the single gate unchanged; new `ROLE_FIX` digest (the old one kept while its runs may still push)
  - honesty: The gate still judges the exact commit the Codex fixer made (same SHA after the hand-off); an export over the size cap fails the try with a clear code and the story goes on
- review-commit runs one review step per reviewer guarded by `when reviewer == <actor>`; the verdict step takes the single review step that succeeded and still derives reviewer and implementer from the store (never the same actor or backend, read-only, trusted digest); `REVIEWER_BACKENDS` gains qwen; a qwen-reviewer actor digest is pinned; new `ROLE_REVIEW` digest
  - honesty: codex-reviewer never reviews a Codex try and qwen-reviewer never reviews a Qwen try; the verdict step refuses otherwise
- A Codex try that fails with `capacity_exhausted` records a cooldown for its level and the story goes on at the next level (the try counts toward the 3)
  - honesty: After `capacity_exhausted` the level is skipped until its cooldown ends, and the try that hit it counts toward the 3

## Honesty conditions

- No try's commit is ever reviewed by the actor or backend that wrote it, enforced at the verdict step from the store, not from routing
- A level that is unavailable never stalls a story: it falls through or hands back once within the 3-try budget
- PR authors see one status comment per story naming each try's level and fixer, and at most one hand-back
- Today every try of the 2026-10-10 stories ran on qwen-fixer and was reviewed by codex-reviewer (live runs)
- A story's try 2 is dispatched to codex-fixer and its commit is reviewed by qwen-reviewer, both from the store's records, not routing hints
- Escalation never adds a try: the 3-try budget per story is unchanged
- No new code path pushes; publish-fix's push placement and trust checks are unchanged
- The status comment of that live story names Qwen for try 1 and Codex for try 2

## Success signals

- A live story whose Qwen try does not pass is retried by Codex, reviewed by Qwen, and either published or handed back once, with the status comment naming both levels

## Scope / boundaries

- The fixer still never pushes or merges; only publish-fix pushes as the App, from spark2; a mis-routed level fails closed at the verdict step

## Non-goals

- The Claude level (needs a cultureagent Claude bridge), more than 3 tries per story, and a level choice by the PR author

## Assumptions

- The operator provisions on spark an unprivileged culture-codex-fixer account with its own codex login, a second cultureagent-codex-bridge (workspace-write, `commit_author` the App bot, `max_concurrent` 1) on its own port, its bridge token in grant, and a sudoers entry for the hand-off export; and registers codex-fixer and qwen-reviewer actors

## Decisions

- Scope of this stack: the engine side (per-try fixer selection, reviewer follows the fixer, per-level pools and queues, fall-through) plus Codex as level 2; Claude as level 3 waits for a cultureagent Claude bridge, filed as a cultureagent issue (operator, 2026-10-10)
- The escalation runs within one story; a new /fix or a human push starts a new story at level 1 with a fresh 3-try budget (operator, 2026-10-10, #39 comment)

## Hard questions

- Codex hand-off: through the store (an export step on spark, an import on spark2; no cultureagent change) or a bundle endpoint added to the cultureagent Codex bridge that spark2 fetches over the tailnet? (resolved: Through the store: an export step on spark writes a size-capped git pack to the store, an import step on spark2 rebuilds a fixer-owned worktree for the unchanged gate; no cultureagent change (operator, 2026-10-10))
- Should qwen-reviewer share the qwen-spark2 pool with qwen-fixer (one cortex server, so a review waits behind fixer tries) or get a separate slot? (resolved: qwen-reviewer shares the qwen-spark2 pool with qwen-fixer: the operator's standing rule is one Qwen job at a time on the cortex server (no parallel Qwen work))
