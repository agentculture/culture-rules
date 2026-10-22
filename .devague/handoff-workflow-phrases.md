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

## Next (2026-10-18, after the rollout)

1. Done: PR #70 merged (2be3b8d); the operator approved e1-e14, b1-b9 and l2; t20 rolled 0.21.0
   out live (rollout verify passed; migrate-notes moved 10 workflows, 51/51 texts byte for byte;
   migrate-orphans had 0), filed as e15 and e16 (approved 2026-10-19). Closeout PR on
   `rules/workflow-phrases-closeout` (0.21.1).
2. Open: watch the next pr-fix chain run on 0.21.0; it must not end in `workflow_not_trusted`
   (t20's last criterion, o8's live half).
3. Open: the Qwen reviewer on spark2 fails with JSON-RPC -32603 within ~15 s of session/prompt
   (runs run-c21a3ad5..., run-8b813e43...); cortex now runs on both Sparks behind the lobes
   model-gear gateways, so check the cortex endpoint in the culture-fixer account's Qwen Code settings.

## Open notes

- Operator declined a Retry button for the describe error.
- Follow-ups recorded as risks: r4 describe.step_text crash on non-numeric retry, r5 web
  StepKind lacks "wait", r7 a Playwright cold-start flake, r8 SSE tabs vs the HTTP/1.1
  6-connection limit.
- Review servers: the real build at 127.0.0.1:5199 (API 8766, launcher in the session
  scratchpad `realdemo/`), the design comparison page at 127.0.0.1:5200 (`variants/`). Stop
  them before closing the session.
