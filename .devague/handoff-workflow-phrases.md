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

## In flight when this was written

- t19 (docs, four prompt files, CHANGELOG, version 0.21.0, closeout table in the evidence
  log) on worktree `../.worktrees.culture-rules/wp-t19`, branch `rules/wp-t19`. Merge with
  the full gate when it reports.
- Qwen review 1 of 2 (trusted.py, service.py, store/revision.py, io/exchange.py), output in
  the session scratchpad `review1.txt`. Then review 2: culture_rules/model/paragraph.py. One
  Qwen job at a time. Verify every finding before acting.

## Next

1. Merge t19, rerun all gates (pytest, vitest, `npm run build` then Playwright, lint, teken,
   markdownlint, scan-secrets).
2. Fold in verified Qwen findings (deviate if they change the plan).
3. `/validate-delivery`: evidence for plan obligations o1-o13 (+ frame o1-o14).
4. PR through the cicd skill (body signed by the cicd script), then `/summarize-delivery`.
5. t20 live rollout from the operator's shell: idle, pause, upgrade every host,
   `workflows migrate-notes` (dry run, --apply, dry run = 0), seed, import,
   `rules migrate-orphans` (dry run, --apply, dry run = 0), enable, resume, verify.

## Open notes

- Operator declined a Retry button for the describe error.
- Follow-ups recorded as risks: r4 describe.step_text crash on non-numeric retry, r5 web
  StepKind lacks "wait", r7 a Playwright cold-start flake, r8 SSE tabs vs the HTTP/1.1
  6-connection limit.
- Review servers: the real build at 127.0.0.1:5199 (API 8766, launcher in the session
  scratchpad `realdemo/`), the design comparison page at 127.0.0.1:5200 (`variants/`). Stop
  them before closing the session.
