# Build Plan — PR fixer escalation

slug: `pr-fixer-escalation` · status: `exported` · from frame: `pr-fixer-escalation`

> PR fixer escalation: within one story, a try that does not pass is retried by a stronger fixer (Qwen, then Codex), each try reviewed by a different model, and an unavailable level falls through instead of stalling

## Tasks

### t1 — Levels: `fixer_levels` and per-level queues resolved at dispatch

- instruction: `culture_rules`/node/actions/queue.py (`_admission`/`_dispatch`), queue-add/queue-progress/queue-stop workflows, pr-fixer-dispatch condition data.queue in vars; reuse limits.`pool_cap` and engine.claims `key_state`
- covers: c8, h7, c9, h8, c5, h6, c3, h4, h2
- acceptance:
  - A shared variable `fixer_levels` (ordered {level, actor, reviewer, pool, queue, enabled}) and `fixer_unavailable` (next|`hand_back`) are seeded in variables.json; level 1 is qwen-fixer/codex-reviewer/qwen-spark2/pr-fixer
  - queue.progress computes the try as the key's count plus one at dispatch, picks that try's level, skips a level whose actor is disabled or deleted, whose pool is over budget or which is in a capacity cooldown, and writes fixer, reviewer, level and attempt into the dispatch event; with no level left it hands back once (`fixer_unavailable` `hand_back`) or keeps waiting never; a story never gets a 4th try
  - Each level's queue is its own queue document on its own pool, first come first served; queue.add routes a retry to its next level's queue; queue.stop and the dispatch rule cover every queue in `fixer_levels`

### t2 — Codex capacity cooldown

- instruction: Extend pr-fixer-retry-failed's match or add pr-fixer-retry-capacity; the cooldown lives in `actor_usage` or a small store document read by queue.progress
- depends on: t1
- covers: c12, h11
- acceptance:
  - A try whose agent step fails `capacity_exhausted` records a cooldown for its level (default 1 h, variable `fixer_capacity_cooldown_s`) and the story's retry goes to the next level; the failed try counts toward the 3

### t3 — Codex hand-off: export on spark, import on spark2

- instruction: Model on gate.py `_import` (pack-objects/index-pack --strict) and its run-as seam; never ssh; the pack stays well under Mongo's 16 MB
- covers: c10, h9
- acceptance:
  - Built-in handoff.export (placed on spark) packs `start_sha`..`head_after` from the Codex fixer's worktree through a run-as seam, refuses over a size cap (`handoff_too_large`) and stores it in a handoffs collection; handoff.import (placed on spark2) fetches the base as culture-fixer, indexes the pack with --fix-thin and creates a fixer-owned worktree whose HEAD is exactly `head_after`
  - Tests prove the gate judges the same SHA the fixer made, a pack over the cap fails the try with `handoff_too_large`, and a corrupt pack fails closed

### t4 — pr-fix: one agent step per level plus the hand-off

- instruction: Keep lineage.`final_gate`'s single-gate rule; avoid two edges into one port (last edge wins)
- depends on: t1, t3
- covers: c4, h5, c2, h3, c14, h12
- acceptance:
  - pr-fix's fix body runs `agent_qwen` (when fixer == qwen-fixer) or `agent_codex` then handoff export/import (when fixer == codex-fixer), then the single gate fed from one source step; outputs implementer and reviewer; pr-fixer-dispatch maps fixer, reviewer and attempt; new `ROLE_FIX` digest added, old kept
  - The status comment names each try's level and fixer (`fixer_status` takes the agent step that ran, not the first); one hand-back per story
  - No new code path pushes: publish-fix and its placement and trust checks are unchanged (bundle test)

### t5 — review-commit: the reviewer follows the fixer

- instruction: `culture_rules`/actors/review.py (`_step_names`, `_judge_chain`, `REVIEWER_BACKENDS`); tests/rules/`test_pr_fixer_bundle.py` expectations
- depends on: t4
- covers: c11, h10, h1
- acceptance:
  - review-commit runs `review_codex` (when reviewer == codex-reviewer) or `review_qwen` (when reviewer == qwen-reviewer); the verdict step reads `review_steps`, takes the one that succeeded and still derives reviewer and implementer from the store, refusing the same actor or backend; `REVIEWER_BACKENDS` gains qwen
  - A qwen-reviewer actor (read-only, plan mode, pool qwen-spark2) is added to docs/rules/pr-fixer/actors and its digest pinned in `TRUSTED_ACTOR_DIGESTS`; new `ROLE_REVIEW` digest, old kept

### t6 — Actors, docs and the operator's setup

- instruction: No secret values; grant names only
- depends on: t2, t5
- covers: c1
- acceptance:
  - codex-fixer actor JSON (machine spark, pool codex-spark, `max_concurrency` 1, workspace-write) in the bundle; docs/operations/pr-fixer.md documents the levels, the hand-off, the reviewer rule, and the operator's setup: the culture-codex-fixer account, its codex login, the second Codex bridge, grant secrets and the sudoers entry; the rollout lists new rules, variables and digests; CHANGELOG and version bump

### t7 — A live story escalates from Qwen to Codex

- instruction: Operator-run setup and rollout; Claude watches the story and files the evidence
- depends on: t6
- covers: c16, h13
- acceptance:
  - After the operator's setup and rollout, a live story whose Qwen try does not pass gets a Codex try reviewed by Qwen and is published or handed back once; its status comment names Qwen for try 1 and Codex for try 2
