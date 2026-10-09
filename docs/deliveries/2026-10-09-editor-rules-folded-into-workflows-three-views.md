# Delivery Summary — Editor: rules folded into workflows, three views

plan: `editor-rules-folded-into-workflows-three-views` · run: `partial` · date: `2026-10-09`
baseline: `devague summary skeleton`

## Intent

> The culture-rules editor folds Rules into Workflows: every rule is shown and edited as a workflow's When entry point or Continues-into link, the tabs become Workflows | Actors | Variables | Statistics, and a workflow opens in a Simple, Detailed or Debug view — with no engine, API or CLI change.

After: Opening a workflow shows, in the Simple view, its When entry points (each rule that starts it: trigger, condition, placement, attempt counting), and its Then: continues into, ends here (the chain-end action), on failure, and runs (key and budget); every field is editable there and saves to the underlying rule.; The Workflows list groups workflows linked by continuations into one chain card (e.g. PR fixer: 4 workflows, 6 entry points, was 9 rules), with a Chain view drawing entry points, workflows and continuation edges.; A Simple / Detailed / Debug switch on the workflow toolbar changes the canvas between the When/Then summary, the steps-and-control-flow graph, and the full port/type/reference graph whose port selection highlights upstream and downstream; the chosen mode persists per viewer.

## Planned Work

- `t1` — Engine proof: a rule whose workflow has no steps behaves as the same rule without a workflow
- `t2` — Pre-fold test inventory and before-state evidence
- `t3` — Fold model: derive entry points, continuations, chains and shared values from rules and workflows
- `t4` — Fold writes: shared-edit fan-out, re-read before write, D7 create with rollback, predecessor rewrite
- `t5` — View switch with Detailed and Debug views
- `t6` — Simple view: When entry points and Then, editable per rule
- `t7` — Workflows list with chain cards and the Chain view
- `t8` — Shell, routing and agent-state: drop the Rules tab, redirect old links, alias the agent-state
- `t9` — Folded tests: port the inventory and add the fold's Playwright scenarios
- `t10` — Docs and harness prompt files describe the four tabs
- `t11` — Boundary check, version bump and PR
- `t12` — Live verification after rollout, including the D7 conversion

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `tests/engine/test_stepless_workflow.py` (3 tests): a stepless workflow behaves as no workflow; run events differ in `workflow_id` and `workflow_version` (d1). No file under `culture_rules/` changed. Codex build, Opus review. |
| `t2` | delivered | `docs/plans/2026-10-09-editor-fold-test-inventory.md`: 374 scenario rows plus webglass G1–G3, and the before state (the PR fixer as 2 chains, d2). Opus build, Codex review. |
| `t3` | delivered | `web/src/fold/model.ts` and its test: entry points, continuations (any, linked or ambiguous), chains, `sharedValues`, D7 candidates, `predecessorTerms`, `SHAREABLE_RULE_FIELDS`. Codex build, two Opus reviews. |
| `t4` | delivered | `web/src/fold/writes.ts` and its test: fan-out with a re-read before each write, D7 with rollback and orphan handling, predecessor rewrite, server-managed fields stripped on rule PUT. Codex build, three Opus reviews with real-server probes. |
| `t5` | delivered | `web/src/workflows/views/`: the Simple / Detailed / Debug switch and a Debug port graph that matches the engine's loop, carry and conditional semantics. Opus build, three Codex reviews. |
| `t6` | delivered | `web/src/workflows/simple/`: the When / Then view with per-rule and shared edits, override and retry results, D7 offer, + Entry point and New rule. Opus build; three Codex and two Opus review rounds; d3. |
| `t7` | delivered | `web/src/workflows/list/`: chain cards, the Chain view, rules without a workflow, the same-event note and focus handling. Codex build, two Opus reviews. |
| `t8` | delivered | Four tabs, `/rules` redirects (`routes/legacy-redirects.ts`), `/workflows/:id`, agent-state entry points with the deprecated `rules` alias, the views and list mounted, New rule and the D7 place. Built by the main agent, two Opus reviews. d6's trusted-save question was added on top. |
| `t9` | delivered | The inventory ported (every row named or kept), the legacy files deleted after porting, and `e2e/fold.spec.ts`, `entry-points.spec.ts` and `entry-pickers.spec.ts`. vitest 653, Playwright 98, webglass locally. Opus build under d4, two Opus reviews. |
| `t10` | delivered | CLAUDE.md, QWEN.md, AGENTS.override.md, AGENTS.colleague.md, .pi/SYSTEM.md, the dated amendment, README.md, web/README.md, docs/demo.md, docs/operations/pr-fixer.md, docs/run-events.md and `tests/test_docs_fold.py`. Opus build, two Opus reviews. |
| `t11` | partial | The boundary check holds (only the two d5 strings changed under `culture_rules/`; `api/` is unchanged), 0.17.0 with its CHANGELOG entry, and every CLAUDE.md gate green locally. The PR is not open yet: it waits for this summary and the queued Codex reviews (d4). |
| `t12` | blocked | Needs the operator's deploy go-ahead after the PR merges: live checks on rules.culture.dev and the D7 conversion of `test-jira-scrum21-to-discord`. |

## Mid-work Decisions

- `d1` — t1 criterion 2 amended: the two rules.run.\* events differ in `workflow_id` AND `workflow_version` (None vs the stepless workflow's version 1), not `workflow_id` alone — t1's engine proof (fold-t1 33c7087) found `run_completions.py` puts workflow.version into data.`workflow_version`; no engine change allowed (c2). No rule or workflow in docs/rules reads data.`workflow_version`, so D7 stays safe; the proof test asserts the two-field difference instead
- `d2` — t3 criterion amended: the PR fixer fixture yields 2 chains (pr-fix, review-commit, publish-fix linked by 3 continuations; report-secrets on its own, started by 2 GitHub-event entry points), not 1 chain — Codex t3 stopped: with c26 (links only from the data.`workflow_id` compare) report-secrets has no continuation link; 4 workflows, 6 entry points, 3 continuations still hold. The plan's '1 chain' miscounted
- `d3` — t6: enable/disable is one POST /rules/{id}/enable|disable (the Rules tab's endpoint, which the stop-runs offer reads), not one PUT; per-entry form saves go through useRulesData/updateRule (not fold/writes) so PUT bodies equal the Rules tab's; D7-orphan and emptied-workflow cleanup call deleteWorkflowDef directly — criterion 3 says 'enable, disable and rename touch one rule (one PUT)' and the instruction says 'write through t4 only'; the Rules tab toggles via POST and stop-runs depends on that answer, and saveSharedEdit refuses name/enabled by design (c28); one rule, one write is still asserted by test
- `d4` — Codex usage limit until 19:16: Codex-owned work falls back to a fresh Opus subagent (t6 third review now; t9 build; t8 review), with a Codex review of each queued after the reset — codex exec returned 'You've hit your usage limit ... try again at 7:16 PM' during t6's third review; the handoff's fallback is an Opus subagent with a Codex review afterwards (reviewer stays a different agent instance from the builder)
- `d5` — d5: the CLI learn text (`culture_rules`/cli/`_commands`/learn.py) and the explain catalog (`culture_rules`/explain/catalog.py) change their 'five tabs (Rules | ...)' description strings to the four tabs; no behaviour change; t11's boundary check allows exactly these strings — CLAUDE.md requires the CLI self-descriptions to match it; c2/c12 forbid `culture_rules`/ changes; operator chose to allow the two strings
- `d6` — d6: new small task (main agent builds, Opus reviews): saving a trusted workflow (pr-fix, review-commit, publish-fix) from Detailed or Debug asks for confirmation first, naming the trust it loses; trusted ids mirrored in a web constant with a test keeping them in step with `culture_rules`/actors/trusted.py — confirmed assumption c32 says the editor should warn before saving a trusted workflow; no plan task covered it (assumptions are not coverage targets); t10 found no warning exists; operator chose to add it now

- The legacy Rules board was kept, unmounted, as a test oracle between t8 and t9, then deleted once t9 hard-coded the bodies it sent (`RULES_TAB`, verified against the board by the t9 reviewer). No deviation record covers this, because it stayed inside both tasks' contracts.
- Wave 2 started t7 before t4 merged, because t7 depends only on t3. This followed the plan's graph more closely than its wave grouping.
- Reviews found an existing bug on `main`: every Rules-tab save sent `updated_at` and got a 422 from the strict rule PUT. t4 fixed it in `updateRule` for every caller.
- Reviews found that `DELETE /workflows/{id}` soft-deletes a workflow even while a rule still uses it. That is a server gap, out of scope under c2, so the editor re-reads `GET /rules` before deleting.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|------------------------|-----------------|
| `t1` (`d1`) | t1's engine proof (fold-t1 33c7087) found `run_completions.py` puts workflow.version into data.`workflow_version`; no engine change allowed (c2). No rule or workflow in docs/rules reads data.`workflow_version`, so D7 stays safe; the proof test asserts the two-field difference instead | `acceptable` |
| `t3` (`d2`) | Codex t3 stopped: with c26 (links only from the data.`workflow_id` compare) report-secrets has no continuation link; 4 workflows, 6 entry points, 3 continuations still hold. The plan's '1 chain' miscounted | `acceptable` |
| `t6` (`d3`) | criterion 3 says 'enable, disable and rename touch one rule (one PUT)' and the instruction says 'write through t4 only'; the Rules tab toggles via POST and stop-runs depends on that answer, and saveSharedEdit refuses name/enabled by design (c28); one rule, one write is still asserted by test | `acceptable` |
| `t9` (`d4`) | codex exec returned 'You've hit your usage limit ... try again at 7:16 PM' during t6's third review; the handoff's fallback is an Opus subagent with a Codex review afterwards (reviewer stays a different agent instance from the builder) | `acceptable` |
| `t11` (`d5`) | CLAUDE.md requires the CLI self-descriptions to match it; c2/c12 forbid `culture_rules`/ changes; operator chose to allow the two strings | `acceptable` |
| `t6` | o3 lists inputs mapping, priority and exclusive group as editable with Rules-tab parity; neither the Simple view nor the old Rules tab edits them, so parity holds only vacuously (evidence e4: fail; delta b6) | `needs-follow-up` |
| `t11` | the PR has not opened yet: the operator's goal puts /validate-delivery and /summarize-delivery first, and the d4 Codex reviews are queued until the 19:16 reset | `acceptable` |
| `t12` | not run: needs the deploy go-ahead after merge (as planned) | `needs-follow-up` |
| `t8` (`d6`) | confirmed assumption c32 says the editor should warn before saving a trusted workflow; no plan task covered it (assumptions are not coverage targets); t10 found no warning exists; operator chose to add it now | `needs-follow-up` |

## Evidence

Run at commit `4c42240` (2026-10-09T14:21Z) unless noted. The evidence records e1–e20 and deltas b1–b8 are filed, proposed, pending adjudication.

- tests: `uv run pytest -n auto --cov=culture_rules` — pass (4598 passed, 9 skipped), run before the bump commit on the same tree
- tests: `tests/engine/test_stepless_workflow.py`, `tests/server/test_openapi_contract.py`, `tests/test_docs_fold.py`, `tests/test_docs_t40.py`, `tests/rules/test_trusted_role_ids.py` — pass (46)
- tests: `cd web && npx vitest run` — pass (653 / 653, on repeated full runs)
- tests: `cd web && npx playwright test` — pass (98 / 98); `e2e/fold.spec.ts` and `e2e/shell.spec.ts` re-run at `4c42240` — pass (11)
- lint: black, isort, flake8 and bandit over `culture_rules tests`; `markdownlint-cli2` over the tree (the untracked `.devague/reviews/` artifact excluded); `scripts/scan-secrets.py`; `npm run typecheck`; `npm run check` — all pass
- gates: `teken cli doctor . --strict` 26/26; `scripts/harness-smoke.py --stage config` 6/6; `culture-rules doctor` healthy
- packaging: `CULTURE_RULES_REQUIRE_WEB=1 uv build --wheel` — `culture_rules-0.17.0` with 12 `web_dist` files
- boundary: `git diff origin/main...HEAD -- culture_rules api` — only `learn.py` and `catalog.py` description strings (d5)
- unmet: e4 (fail) — no editor for priority, exclusive_group or the inputs mapping
- not checked: o11 (c18 live) — no evidence filed; t12
- commits: `origin/main` (9d72b1e)..`e04910a` on `rules/editor-fold-spec` (60 commits)
- PRs / issues: none yet; related #26 (a rule to visualize a spec on its PR)

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| `c1` — The culture-rules editor folds Rules into Workflows: every rule is shown and edited as a workflow's When entry point or Continues-into link, the tabs become Workflows \| Actors \| Variables \| Statistics, and a workflow opens in a Simple, Detailed or Debug view — with no engine, API or CLI change. | medium | `web/src/App.test.tsx::has exactly four top-level tabs, in order; web/e2e/shell.spec.ts::exactly four top-level tabs (Rules folded into Workflows) and no runs/history route` (run 2026-10-09) |
| `c2` — Editor only: no change to the engine, model, API, CLI or MCP; rules stay rule objects with their stored shape. The one stored change is D7: a workflow created (through the existing endpoints) for each rule that has none. | high | `git diff origin/main...HEAD -- culture_rules api; tests/server/test_openapi_contract.py` (run 2026-10-09) |
| `c9` — Opening a workflow shows, in the Simple view, its When entry points (each rule that starts it: trigger, condition, placement, attempt counting), and its Then: continues into, ends here (the chain-end action), on failure, and runs (key and budget); every field is editable there and saves to the underlying rule. | medium | `web/src/workflows/simple/SimpleView.test.tsx::the edit form / adding a condition / a relationship / removing a relationship / enable / disable (RULES_TAB oracle); EntryPoints.test.tsx::edits name, trigger, action and placement and saves with PUT` (run 2026-10-09) |
| `c10` — The Workflows list groups workflows linked by continuations into one chain card (e.g. PR fixer: 4 workflows, 6 entry points, was 9 rules), with a Chain view drawing entry points, workflows and continuation edges. | high | `web/src/workflows/list/list.test.tsx::renders the checked-in PR fixer as two cards with measured d2 counts; ::draws all fixture starts, workflows and directed continuations, including the return edge; web/e2e/fold.spec.ts::the list folds the stored PR fixer` (run 2026-10-09) |
| `c11` — A Simple / Detailed / Debug switch on the workflow toolbar changes the canvas between the When/Then summary, the steps-and-control-flow graph, and the full port/type/reference graph whose port selection highlights upstream and downstream; the chosen mode persists per viewer. | medium | `web/src/workflows/views/WorkflowViews.test.tsx (8 tests); web/src/routes/WorkflowsFolded.test.tsx::a workflow opens in the Simple view ... with the switch to Detailed and Debug` (run 2026-10-09) |
| `c12` — No change to `culture_rules` (model, engine, API, store), api/openapi.json, the CLI or the MCP tools; every edit the editor makes goes through the existing rule and workflow endpoints. | high | `git diff origin/main...HEAD; web/src/fold/writes.ts uses only the existing api client; tests/server/test_openapi_contract.py` (run 2026-10-09) |
| `c15` — Every existing rule remains reachable and editable as an entry point (or continuation) of a workflow; a rule with no workflow is given its own stored workflow with no steps (D7), offered by the editor for existing rules and done automatically when an action-only rule is created. | high | `tests/engine/test_stepless_workflow.py (3 tests)` (run 2026-10-09) |
| `c16` — Old /rules and /rules/:ruleId links redirect to the workflow (and entry point) that shows the rule, or to the place for workflow-less rules; the catch-all route lands on /workflows. | high | `web/src/routes/legacy-redirects.test.ts (7); web/src/App.test.tsx redirect tests (4); web/e2e/fold.spec.ts::old /rules links redirect` (run 2026-10-09) |
| `c17` — CLAUDE.md, QWEN.md, AGENTS.override.md, AGENTS.colleague.md, .pi/SYSTEM.md (where relevant) and the engine-editor spec describe the four tabs in step; harness-smoke and teken doctor stay green. | high | `tests/test_docs_fold.py; tests/test_docs_t40.py; scripts/harness-smoke.py --stage config; teken cli doctor . --strict; culture-rules doctor` (run 2026-10-09) |
| `c18` — For the live PR fixer, the Workflows tab shows 1 chain card with 4 workflows and 6 entry points, and editing an entry point's condition saves exactly 1 rule (verified by a Playwright test against the fake API and once live). | unverified | (no evidence: needs the live check, t12, and the Playwright half is counted under c25) |
| `c19` — 0 regressions: every rules and workflows Playwright and vitest scenario that existed before passes in its folded form, and the webglass agent-state gate stays green. | medium | `docs/plans/2026-10-09-editor-fold-test-inventory.md t9 column; full vitest (653), Playwright (98), webglass G1/G2 replay` (run 2026-10-09) |
| `c25` — An edit to a shared (identical) value writes every entry point's rule through the existing endpoint, one rule at a time; the editor shows which rules saved and which did not, keeps the failed ones marked as overrides with the old value, and offers a retry, so a partial failure is visible, never silent. | high | `web/e2e/fold.spec.ts::a fan-out with one failing PUT shows that rule as an override with its old value and a retry; web/src/workflows/simple/SimpleView.test.tsx::shows a partial failure per rule ...; web/src/fold/writes.test.ts::saves rule by rule, returns failures with the old value, and supports retry` (run 2026-10-09) |
| `c26` — Chain links are derived only from a continuation rule's condition holding `data.workflow_id == <id>` (an all-term compare, as docs/rules/pr-fixer/rules/pr-fixer-review-commit.json does); a run-event rule without that term shows as 'continues from any workflow', and the predecessor is edited with a dedicated control that writes exactly that compare, so a condition edit cannot silently drop a link. | high | `web/src/fold/model.test.ts; web/src/fold/writes.test.ts::rewrites only the all-term workflow compare; web/src/workflows/list/list.test.tsx::uses dynamic counts and names, preserving ambiguous, unscoped and empty predecessors; SimpleView.test.tsx::rewrites exactly the data.workflow_id compare` (run 2026-10-09) |
| `c27` — Before each rule write of a shared edit, the editor re-reads the rule and compares it with the snapshot the edit was made on; a rule changed meanwhile is skipped and flagged (rule PUT in `culture_rules`/server/app.py has no If-Match or revision check, so writes are last-wins and a fan-out would otherwise overwrite rules the user is not looking at). | high | `web/e2e/fold.spec.ts::a concurrent change is re-read, skipped and flagged, never overwritten; web/src/fold/writes.test.ts::never PUTs a rule changed since the edit snapshot` (run 2026-10-09) |
| `c28` — Identity and lifecycle fields (id, name, description, enabled) are never shared: enabling, disabling or renaming one entry point touches only its rule, and a disabled entry point stays visible, marked disabled. | high | `web/src/workflows/simple/SimpleView.test.tsx::renaming an entry point issues one PUT, for its rule only; ::disabling one entry point writes its rule only, and it stays listed and marked` (run 2026-10-09) |
| `c29` — Every Rules-tab feature beyond the form stays reachable per entry point: history (GET /rules/{id}/history), describe (GET /rules/{id}/describe) and stop runs (POST /rules/{`rule_id`}/stop-runs, StopRunsNotice). | high | `web/src/workflows/simple/SimpleView.test.tsx::history reads GET /rules/{id}/history; ::describe reads GET /rules/{id}/describe; ::disabling with runs going offers to stop them; EntryPoints.test.tsx stop-runs tests` (run 2026-10-09) |
| `c30` — D7 is two writes (create the workflow, then point the rule at it): if the second fails, the editor deletes the workflow it just created or shows it as an orphan with a fix action; deleting the last entry point of a stepless D7 workflow offers to delete that workflow too. | high | `web/src/fold/writes.test.ts D7 tests (9); web/src/workflows/simple/SimpleView.test.tsx::a failed attach deletes the new workflow again; ::an orphan wrapper is flagged with a fix action; ::deleting the last entry point of a stepless workflow offers to delete the workflow too` (run 2026-10-09) |
| `c31` — Docs that describe the Rules tab move with the fold: README.md, web/README.md, docs/demo.md (executed walkthrough), docs/operations/pr-fixer.md, docs/run-events.md. | high | `tests/test_docs_fold.py; markdownlint-cli2 over the tree` (run 2026-10-09) |
| `c32` — Saving a workflow from Detailed or Debug changes its digest, so a workflow pinned as trusted (actors/trusted.py roles: pr-fix, review-commit, publish-fix) stops being trusted; this is today's behaviour, unchanged by the fold, but the editor should warn before saving a trusted workflow. | high | `web/src/routes/WorkflowsCreate.test.tsx::Save on a trusted workflow warns ...; ::Escape closes the question; ::opening another workflow closes the question; ::an untrusted workflow saves at once; web/src/workflows/trusted.test.ts; tests/rules/test_trusted_role_ids.py` (run 2026-10-09) |

Caps on confidence:

- **c1 medium:** checked against the built bundle, not on rules.culture.dev.
- **c9 medium:** e4 fails for three fields.
- **c11 medium:** the Debug wires were never checked in a browser (pending l3).
- **c19 medium:** the GitHub Actions web job has not run (pending l9).

Lapse ledger evidence:

pending approval (not yet evidence): `l1`, `l2`, `l3`, `l4`, `l5`, `l6`, `l7`, `l8`, `l9`

## Remaining Work / Follow-up

- **t11, the PR.** The queued Codex reviews of t6, t8, t9, t10 and d6 (d4) run after the 19:16 reset. Then open the PR through cicd and get CI green, including the web job (closes c19's l9). Owner: the main agent.
- **t12, live verification and D7 conversion.** After merge, with the operator's deploy go-ahead: check rules.culture.dev (four tabs, the PR fixer as 2 chain cards per d2, a condition edit makes 1 PUT, old `/rules` links), then run D7 on `test-jira-scrum21-to-discord`. Re-run the run-event probe right before it. Owner: the main agent; the go-ahead is the operator's.
- **o3 / e4.** Decide whether priority, exclusive_group and the inputs mapping get editors (a follow-up issue), or amend c9's field list. Owner: the operator.
- **Adjudication.** The operator confirms or rejects evidence e1–e20, deltas b1–b8 and lapses l1–l9.
- **Server gap.** `DELETE /workflows/{id}` should refuse a workflow a rule still uses. That is an API change, out of scope under c2. Issue to file if the operator agrees.
- **Fixed by the fold, not separately on `main`.** The `updated_at` 422 on rule saves. Patch `main` separately only if the fold's release is delayed. Owner: the operator.
- **Minor follow-ups.** Two copies of the rules data remain (the list's and the Simple view's). Three things the Rules board drew are not drawn (the skipped-run icon, the relationship variable chip, the machine colour on placement). Import can overwrite a trusted workflow without the d6 question (it has its own plan step). The demo screenshots predate the fold.
