# Editor: rules folded into workflows, three views

> The culture-rules editor folds Rules into Workflows: every rule is shown and edited as a workflow's When entry point or Continues-into link, the tabs become Workflows | Actors | Variables | Statistics, and a workflow opens in a Simple, Detailed or Debug view — with no engine, API or CLI change.
> instruction: Verify on rules.culture.dev after rollout: walk the PR fixer chain in Simple, Detailed and Debug; open an old `/rules/<id>` link; check git diff of the delivery stays under web/, the harness prompt files and docs.

## Audience

- The operator and mesh agents who author and inspect automation in the web editor at rules.culture.dev (and locally); secondarily the agents that read the harness prompt files describing the editor.

## Before → After

- Before: A rule and the workflow it starts live on two tabs: to understand the PR fixer you read 9 rules on the Rules tab and 4 workflows on the Workflows tab and join them in your head; the workflow canvas shows one zoom-dependent picture mixing flow and data ports.
- After: Opening a workflow shows, in the Simple view, its When entry points (each rule that starts it: trigger, condition, placement, attempt counting), and its Then: continues into, ends here (the chain-end action), on failure, and runs (key and budget); every field is editable there and saves to the underlying rule.
- After: The Workflows list groups workflows linked by continuations into one chain card (e.g. PR fixer: 4 workflows, 6 entry points, was 9 rules), with a Chain view drawing entry points, workflows and continuation edges.
- After: A Simple / Detailed / Debug switch on the workflow toolbar changes the canvas between the When/Then summary, the steps-and-control-flow graph, and the full port/type/reference graph whose port selection highlights upstream and downstream; the chosen mode persists per viewer.

## Why it matters

- Rules and workflows describe one piece of automation; showing them apart hides what starts a workflow, what it continues into and how it ends, and a single canvas picture is too busy for reading the flow yet too thin for debugging data.

## Requirements

- Every existing rule remains reachable and editable as an entry point (or continuation) of a workflow; a rule with no workflow is given its own stored workflow with no steps (D7), offered by the editor for existing rules and done automatically when an action-only rule is created.
  - honesty: Engine behaviour of a rule whose workflow has no steps equals that of the same rule without a workflow: same action, same params resolution (trigger.\* refs), same run outcome; verified by an engine test before the editor builds D7.
  - honesty: A D7 rule's runs and run events differ from before only in `workflow_id` (now the wrapper's id): no rule that listens to rules.run.\* events without a `workflow_id` filter starts firing because of it (checked against the live rule set).
- Old /rules and /rules/:ruleId links redirect to the workflow (and entry point) that shows the rule, or to the place for workflow-less rules; the catch-all route lands on /workflows.
  - honesty: Each old `/rules/:ruleId` resolves to `/workflows/<workflow>?entry=<rule>` (or the D7 workflow) and a Playwright test covers a known id and an unknown id.
- CLAUDE.md, QWEN.md, AGENTS.override.md, AGENTS.colleague.md, .pi/SYSTEM.md (where relevant) and the engine-editor spec describe the four tabs in step; harness-smoke and teken doctor stay green.
  - honesty: harness-smoke --stage config, teken cli doctor --strict and the doctor prompt checks pass with the new wording, and the engine-editor spec carries a dated amendment instead of silently changed text.
- An edit to a shared (identical) value writes every entry point's rule through the existing endpoint, one rule at a time; the editor shows which rules saved and which did not, keeps the failed ones marked as overrides with the old value, and offers a retry, so a partial failure is visible, never silent.
  - honesty: A Playwright test with the fake API failing one of N rule writes shows the failed rule as an override with its old value and a retry, and the other N-1 saved.
- Chain links are derived only from a continuation rule's condition holding `data.workflow_id == <id>` (an all-term compare, as docs/rules/pr-fixer/rules/pr-fixer-review-commit.json does); a run-event rule without that term shows as 'continues from any workflow', and the predecessor is edited with a dedicated control that writes exactly that compare, so a condition edit cannot silently drop a link.
  - honesty: A test proves a continuation is linked only by its data.`workflow_id` compare, a run-event rule without it shows 'continues from any workflow', and changing the predecessor rewrites exactly that compare (obligation o14).
- Before each rule write of a shared edit, the editor re-reads the rule and compares it with the snapshot the edit was made on; a rule changed meanwhile is skipped and flagged (rule PUT in `culture_rules`/server/app.py has no If-Match or revision check, so writes are last-wins and a fan-out would otherwise overwrite rules the user is not looking at).
  - honesty: A test with one rule changed after the snapshot proves it is re-read, skipped and flagged, never overwritten, while the others save (obligation o15).
- Identity and lifecycle fields (id, name, description, enabled) are never shared: enabling, disabling or renaming one entry point touches only its rule, and a disabled entry point stays visible, marked disabled.
  - honesty: A test proves toggling or renaming one entry point issues one PUT for its rule only, and a disabled entry point stays listed and marked (obligation o16).
- Every Rules-tab feature beyond the form stays reachable per entry point: history (GET /rules/{id}/history), describe (GET /rules/{id}/describe) and stop runs (POST /rules/{`rule_id`}/stop-runs, StopRunsNotice).
  - honesty: A test proves every entry point reaches rule history, describe and stop-runs through the same endpoints the Rules tab used (obligation o17).
- D7 is two writes (create the workflow, then point the rule at it): if the second fails, the editor deletes the workflow it just created or shows it as an orphan with a fix action; deleting the last entry point of a stepless D7 workflow offers to delete that workflow too.
  - honesty: Tests prove a failed second D7 write leaves no unflagged orphan workflow, and deleting the last entry of a stepless D7 workflow offers to delete it (obligation o18).
- Docs that describe the Rules tab move with the fold: README.md, web/README.md, docs/demo.md (executed walkthrough), docs/operations/pr-fixer.md, docs/run-events.md.
  - honesty: README.md, web/README.md, docs/demo.md, docs/operations/pr-fixer.md and docs/run-events.md describe the folded editor and pass markdownlint (obligation o19).

## Honesty conditions

- After delivery the live editor has no Rules tab, every live rule is reachable from Workflows, the three-mode switch works on every workflow, and no engine/API/CLI file changed (the D7 wrapper workflows are data created through existing endpoints).
- The audience named is who actually opens the editor today (the operator on rules.culture.dev, and agents through the harness prompt files that describe it).
- Today the PR fixer is 9 rules on the Rules tab and 4 workflows on the Workflows tab, with no in-editor link from a workflow to the rules that start it (true of web/src/workflows at main).
- The operator confirmed the fold and the three views on the canvas (v11) because the split view hid what starts and continues a workflow.
- Every rule field the Rules tab edits today (trigger, condition, workflow inputs mapping, action, `on_failure`, placement, key, budget, relationships, enabled) is editable from the Simple view, and a save produces the same rule document the Rules tab would have.
- Chains are derived only from stored rules (a rule whose trigger is a run event of workflow X and whose workflow is Y links X to Y); no new stored field.
- Detailed renders exactly today's steps-and-edges graph; Debug renders every input/output port and reference; selecting a port highlights its upstream and downstream ports; the mode is kept per viewer in browser storage (localStorage) only.
- git diff of the delivery touches no file under `culture_rules`/ except the web bundle, and api/openapi.json is unchanged (tests/server/`test_openapi_contract.py` passes untouched).
- The count 1 chain / 4 workflows / 6 entry points is computed from the live rule set (after #22 the fixer has 9 rules: 6 entry points incl. secrets, 3 continuations), not hard-coded.
- The pre-fold test inventory is listed in the plan and each scenario maps to a folded test; none is deleted without a replacement.

## Success signals

- For the live PR fixer, the Workflows tab shows 1 chain card with 4 workflows and 6 entry points, and editing an entry point's condition saves exactly 1 rule (verified by a Playwright test against the fake API and once live).
- 0 regressions: every rules and workflows Playwright and vitest scenario that existed before passes in its folded form, and the webglass agent-state gate stays green.

## Scope / boundaries

- No change to `culture_rules` (model, engine, API, store), api/openapi.json, the CLI or the MCP tools; every edit the editor makes goes through the existing rule and workflow endpoints.

## Non-goals

- Merging the rule and workflow data models, or turning rules into workflow steps; D7's wrapper workflow holds no steps and the rule keeps its trigger, condition and action.
- New engine semantics for shared conditions, workflow-level placement or workflow-level chain-end actions; anything 'shared' in the editor is a presentation of identical per-rule values.

## Assumptions

- The operator's six canvas questions can be answered as editor presentation choices without engine changes.
- Saving a workflow from Detailed or Debug changes its digest, so a workflow pinned as trusted (actors/trusted.py roles: pr-fix, review-commit, publish-fix) stops being trusted; this is today's behaviour, unchanged by the fold, but the editor should warn before saving a trusted workflow.

## Scope exploration

- `s1` — `web/src/App.tsx, web/src/components/Header.tsx`: Five routes and nav links today: /rules/:ruleId? (also the catch-all redirect target), /workflows, /actors, /variables, /statistics. Header.tsx lists Rules first.
- `s2` — `web/src/rules (16 files) and web/src/routes/Rules.tsx, rules-view.ts`: The rule editor is self-contained: RuleList, Forms, TriggerPicker (437 lines), ActionPicker (854), Relationships, StopRunsNotice, useRulesData; the pickers and forms can be reused inside a workflow's When panel.
- `s3` — `web/src/workflows (Canvas.tsx, layout.ts, zoom.ts, nodes.tsx, StepEditor, IoEditor, RunForm, PlacementEditor)`: The workflow editor is a React Flow canvas with zoom levels (zoom.ts) and step/IO editors; it has no notion of which rules start it today.
- `s4` — `web/e2e (rules.spec.ts, rules-pickers.spec.ts, shell.spec.ts) and web/src/**/*.test.tsx`: Playwright and vitest suites address the Rules tab by name and route (/rules, navigation 'Rules'); they move with the fold. The webglass agent-state gate also runs in the CI web job.
- `s5` — `CLAUDE.md, QWEN.md, AGENTS.override.md, AGENTS.colleague.md, docs/specs/2026-10-03-culture-rules-engine-editor.md`: All four harness prompt files and the engine-editor spec state exactly five tabs (Rules | Workflows | Actors | Variables | Statistics); the fold must update them in step (the harness-smoke probes depend on load-bearing phrases).
- `s6` — `design canvas Jgm3JPnAhKWpeiCxFXvNBi v11 (Fold-Editor, Fold-List, Fold-Chain, WF-Flow, WF-Variables, WF-Variables-Port)`: Approved by the operator: Fold-Editor = Simple view (When entry points / Then: continues into, ends here, on failure, runs), WF-Flow = Detailed, WF-Variables(+Port) = Debug; Fold-List and Fold-Chain as drawn. Open questions drawn on the boards: shared entries for one event, continuation ownership, order/limits level, placement level, chain-end comments, shared guard conditions.
- `s7` — `culture_rules model: Rule (trigger, condition, workflow optional, action required, relationship, placement, key/budget)`: Domain model: a rule's condition and workflow are optional, its action is required. So some rules have no workflow (action-only) and need a home in a Workflows-only editor.
- `s8` — `challenge pass / reversibility lens: web bundle + D7 data`: Clean: rollback is reinstalling the previous wheel (the bundle ships in it); D7 wrapper workflows stay valid for the old editor, which shows them as stepless workflows and their rules with a workflow. Nothing to undo in the engine.
- `s9` — `challenge pass / security lens: culture_rules/server/app.py rule and workflow PUTs`: Clean: the fold uses the same endpoints and roles; a fan-out write needs the same write role per rule as a single edit. No new privilege. Residual: the multi-write is not atomic (routed to the fan-out requirements).
- `s10` — `challenge pass / adjacent-systems lens: docs/rules/pr-fixer/rules/pr-fixer-review-commit.json, culture_rules/model/refs.py, culture_rules/model/rule.py WorkflowRef`: Continuations name their predecessor only inside the condition (data.`workflow_id` compare); trigger.\* refs stay valid on a rule with a workflow (refs.py NAMESPACES), so D7 keeps action params working. Seeded the chain-derivation requirement and the run-event honesty on c15.
- `s11` — `challenge pass / overlooked-actors lens: web/src/agent-state/store.ts, docs, Rules-tab features`: Agents consume the agent-state tab/rules contract (question raised); five docs describe the Rules tab; history, describe and stop-runs live only on the Rules tab today. Seeded the docs and features requirements.
- `s12` — `challenge pass / observability lens: editor saves`: Not examined in depth: the editor has no client-side telemetry; partial-save visibility is covered by c25. Residual: no server-side audit beyond rule history.
- `s13` — `challenge pass / cheap probe: live rule set via the API (11 rules)`: All 3 live run-event rules (pr-fixer-publish, pr-fixer-refix, pr-fixer-review-commit) filter on data.`workflow_id`, so a D7 wrapper's run events start none of them today; h15 holds for the live set at 2026-10-09, re-check at D7 time.
  - seeds: `c15`

## Decisions

- Editor only: no change to the engine, model, API, CLI or MCP; rules stay rule objects with their stored shape. The one stored change is D7: a workflow created (through the existing endpoints) for each rule that has none.
- The workflow view switch has three modes: Simple = the When / Then workflow (canvas Fold-Editor), Detailed = steps and control flow (canvas WF-Flow), Debug = every port, type and reference with upstream/downstream highlighting on port selection (canvas WF-Variables, WF-Variables-Port).
- The Workflows list and the chain view are built as drawn on canvas Fold-List and Fold-Chain.
- Primary tabs become Workflows | Actors | Variables | Statistics; there is no Rules tab.
- D1: one entry point per workflow; an entry notes when the same event also starts another workflow.
- D2: a continuation is owned and edited as an entry point of the workflow it starts; the previous workflow's Then shows a read-only Continues-into link.
- D3-D6 (order and limits, placement, chain-end and failure actions, guard conditions): shared when identical: a value every entry point's rule holds identically shows once at workflow level, and editing it writes every entry's rule; a differing value shows per entry point, flagged as an override.
- D7: a rule with no workflow gets a real stored workflow of its own with no steps yet (its trigger and condition are the When, its action the Then), created through the existing workflow and rule endpoints, ready for steps later.
- q8: the agent-state contract keeps tab 'rules' and AgentRulesState as a deprecated alias for one release; 'workflows' carries the entry-point state, and the webglass gate checks both until the alias is removed.

## Open parks

- [unknown_nonblocking] Live data beyond the PR fixer and the one action-only rule may hold shapes the fold has not been designed against (rules with must/may-after or supersedes across different workflows, a rule whose workflow is pinned to an old version)
