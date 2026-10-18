# Handoff: workflow-connecting-phrases (issue #69), 2026-10-18

Branch `rules/workflow-phrases` (worktree `../.worktrees.culture-rules/workflow-phrases`), not
pushed, no PR yet. Spec `docs/specs/2026-10-17-workflow-connecting-phrases.md`, plan
`docs/plans/2026-10-17-workflow-connecting-phrases.md`, split
`docs/plans/2026-10-17-workflow-connecting-phrases-split.md`. Deviations d1-d15, risks
r1-r8, lapses l1-l2 are in `.devague/` (deliveries and plans JSON). Evidence log:
`.devague/evidence-log-workflow-phrases.md`.

## Done and merged on the branch (all gates green at 5af203a)

t1-t18 plus the operator's additions: t21 compact collapsible pane (d6), t22 ported tests
(d9), t23 restored Detailed editor behaviours (d10), d8 disabled marking, d11 one sentence
per line / no colons / stand-ins, d12 pr-fix step renames + new v2 pin, d13 (siblings in one
sentence, loop body own sentence, "empty", CLI "(disabled)", `rules migrate-orphans`,
overview cards only and full width, notes on top of Detailed, header switch, chain link),
d14 (Simple opens the step panel and the rule pane), d15 quiet-inline design. Gates at
5af203a: pytest 6696, vitest 765, Playwright 116, lint, teken 26/26, scan-secrets.

## State (2026-10-18)

- PR #70 open, CI fully green at f6d621a (SonarCloud gate OK, 0 open issues). Not merged.
- Qwen reviews done: fixes 22447cb (workflow-v2 digest helper) and 90ad22f (nested loop body,
  priority guard). Sonar: 79a8b49 (sort compare) and f6d621a (68 smells cleared).
- /validate-delivery: evidence e1-e14, deltas b1-b9 filed (proposed; operator to confirm).
- /summarize-delivery: docs/deliveries/2026-10-17-workflow-connecting-phrases.md (run: partial).

## Next

1. Operator: confirm e1-e14, b1-b9 (and lapse l2); review and merge PR #70.
2. t20 live rollout from the operator's shell: idle, pause, upgrade every host,
   `workflows migrate-notes` (dry run, --apply, dry run = 0), seed, import,
   `rules migrate-orphans` (dry run, --apply, dry run = 0), enable, resume, verify.
3. Live PR fixer: Qwen reviewer fails with JSON-RPC -32603 (two hand-backs on #70).

## Open notes

- Operator declined a Retry button for the describe error.
- Follow-ups recorded as risks: r4 describe.step_text crash on non-numeric retry, r5 web
  StepKind lacks "wait", r7 a Playwright cold-start flake, r8 SSE tabs vs the HTTP/1.1
  6-connection limit.
- Review servers: the real build at 127.0.0.1:5199 (API 8766, launcher in the session
  scratchpad `realdemo/`), the design comparison page at 127.0.0.1:5200 (`variants/`). Stop
  them before closing the session.
