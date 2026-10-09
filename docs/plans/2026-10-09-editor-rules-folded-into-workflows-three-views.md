# Build Plan — Editor: rules folded into workflows, three views

slug: `editor-rules-folded-into-workflows-three-views` · status: `exported` · from frame: `editor-rules-folded-into-workflows-three-views`

> The culture-rules editor folds Rules into Workflows: every rule is shown and edited as a workflow's When entry point or Continues-into link, the tabs become Workflows | Actors | Variables | Statistics, and a workflow opens in a Simple, Detailed or Debug view — with no engine, API or CLI change.

## Tasks

### t1 — Engine proof: a rule whose workflow has no steps behaves as the same rule without a workflow

- instruction: Test-only. Use MemoryStore and the engine entry points the existing tests/engine run tests use; build both rules from the same trigger and action. If behaviour differs, do not patch the engine: report the difference so the operator can decide (D7 depends on it).
- covers: h8, h15
- acceptance:
  - tests/engine/`test_stepless_workflow.py` fires a rule with a stepless workflow and the same rule without one on the same event: same action kind, same resolved params (trigger.\* refs included), same run status
  - the two runs' rules.run.\* events differ only in `workflow_id` (asserted field by field)
  - a rule listening to rules.run.succeeded with a data.`workflow_id` filter does not fire on the stepless workflow's run event
  - no file under `culture_rules`/ changes; if the engine differs, the task stops and reports instead of changing it (a deviation)

### t2 — Pre-fold test inventory and before-state evidence

- instruction: Read-only survey of web/src and web/e2e (grep the test names); write only the inventory doc.
- covers: h12, h2, c7
- acceptance:
  - docs/plans/2026-10-09-editor-fold-test-inventory.md lists every vitest and Playwright scenario under web/ that addresses the Rules tab, rules or workflows, and the webglass agent-state checks, one row each with file and test name
  - each row names the folded test that will replace it (filled by t9), and the file passes markdownlint
  - it records the before state at main: the PR fixer as 9 rules on /rules and 4 workflows on /workflows, with no link from a workflow to its rules

### t3 — Fold model: derive entry points, continuations, chains and shared values from rules and workflows

- instruction: Start from the stored shapes in api/openapi.json and the PR fixer rules in docs/rules/pr-fixer. Keep it framework-free so the list, chain and Simple view all read the same model.
- covers: c10, h5, c26, h16, c28, c15
- acceptance:
  - web/src/fold/model.ts (pure TypeScript, no React) groups rules by workflow into entry points; a rule whose condition holds an all-term data.`workflow_id` compare is a continuation from that workflow; a run-event rule without it is 'from any workflow'
  - chains are connected components of continuation links; for the PR fixer fixture: 1 chain, 4 workflows, 6 entry points, 3 continuations
  - sharedValues() returns, per field, the value all entry points hold identically or 'differs' with the per-rule values; id, name, description and enabled are never shared
  - rules with no workflow are listed as D7 candidates; web/src/fold/model.test.ts covers each criterion

### t4 — Fold writes: shared-edit fan-out, re-read before write, D7 create with rollback, predecessor rewrite

- instruction: Use the existing api client in web/src/api; no new endpoint. Results drive the override/retry UI in t6 (the Simple view), so return data, do not render.
- covers: c25, c27, h17, c30, h20
- acceptance:
  - web/src/fold/writes.ts writes a shared edit rule by rule through the existing rule PUT and returns per-rule results (saved, failed, skipped-changed)
  - before each PUT it re-reads the rule and skips it, flagged, if it differs from the snapshot; a test proves a concurrently changed rule is never overwritten
  - D7: creates the stepless workflow, then points the rule at it; if the rule PUT fails it deletes the workflow it created (or returns it as an orphan when the delete fails)
  - changing a continuation's predecessor rewrites only the data.`workflow_id` compare term; web/src/fold/writes.test.ts covers each criterion against the fake API

### t5 — View switch with Detailed and Debug views

- instruction: Reuse Canvas.tsx, layout.ts and nodes.tsx for Detailed; build Debug as a separate layout. The Simple slot renders a placeholder until t6 (the Simple view) lands; integration happens in t8.
- covers: c11, h6
- acceptance:
  - web/src/workflows/views/ adds a Simple / Detailed / Debug toolbar switch; Detailed renders today's steps-and-edges canvas unchanged
  - Debug renders every input and output port with its type and reference; selecting a port highlights its upstream and downstream ports (canvas WF-Variables, WF-Variables-Port)
  - the chosen mode persists per viewer in localStorage behind try/catch; vitest covers switch, highlighting and persistence

### t6 — Simple view: When entry points and Then, editable per rule

- instruction: Read the model from t3 and write through t4 only. Keep the Rules-tab components where they are and import them; do not move files (t8 removes the tab).
- depends on: t3, t4
- covers: c9, h4, h18, c29, h19
- acceptance:
  - web/src/workflows/simple/ renders When (each entry point: trigger, condition, placement, attempt counting, enabled) and Then (continues into as read-only links, ends here, on failure, runs) as on canvas Fold-Editor
  - every rule field the Rules tab edits is editable here, reusing web/src/rules TriggerPicker, ActionPicker and Forms, and saving produces the same rule document the Rules tab did (vitest compares the PUT bodies)
  - shared values show once, differing ones per entry as overrides; enable, disable and rename touch one rule (one PUT); per-entry history, describe and stop-runs work
  - partial fan-out failures show per rule with the old value and a retry; a D7 candidate shows an offer to create its workflow

### t7 — Workflows list with chain cards and the Chain view

- instruction: Replace WorkflowList.tsx's body behind the same props where possible, so t8's integration stays small.
- depends on: t3
- covers: c10, h11
- acceptance:
  - web/src/workflows/list/ renders chain cards (Fold-List): name, workflow count, entry point count, 'was N rules', and per workflow its starts, continues into, runs and ends with
  - a Chain view (Fold-Chain) draws entry points, workflows and continuation edges; counts come from the model, never hard-coded
  - vitest renders the PR fixer fixture as 1 chain card with 4 workflows and 6 entry points

### t8 — Shell, routing and agent-state: drop the Rules tab, redirect old links, alias the agent-state

- instruction: Integration only: wire t5-t7 together and remove the tab. Keep the redirect table in one module with a unit test.
- depends on: t5, t6, t7
- covers: c16, h22
- acceptance:
  - Header.tsx and App.tsx show Workflows | Actors | Variables | Statistics; the catch-all lands on /workflows
  - /rules and /rules/:ruleId redirect to /workflows/:workflowId?entry=:ruleId (or the D7 candidate place); an unknown id lands on /workflows with a not-found notice
  - web/src/agent-state reports tab 'workflows' with the entry-point state and keeps 'rules' and AgentRulesState as a deprecated alias for one release (decision c33)
  - routes/Workflows.tsx mounts the list (t7) and the view switch (t5) with the Simple view (t6), Detailed and Debug; routes/Rules.tsx is removed

### t9 — Folded tests: port the inventory and add the fold's Playwright scenarios

- instruction: Move rules.spec.ts and rules-pickers.spec.ts scenarios into the folded flows rather than deleting them.
- depends on: t8, t2
- covers: c19, c18, h14
- acceptance:
  - every inventory row (t2) has a passing folded counterpart, recorded in the inventory doc; none is deleted without one
  - Playwright against the fake API: editing one entry point's condition issues exactly 1 rule PUT; a fan-out with one failing PUT shows that rule as an override with its old value and a retry while the others saved; a concurrent change is skipped and flagged; old /rules links redirect
  - the CI web job (typecheck, vitest, Playwright, webglass agent-state gate) is green

### t10 — Docs and harness prompt files describe the four tabs

- instruction: The audience (c6) is the operator and the agents reading these prompt files: write the tab description for both.
- depends on: t8
- covers: c17, h10, c31, h21, c6, h1
- acceptance:
  - CLAUDE.md, QWEN.md, AGENTS.override.md, AGENTS.colleague.md and .pi/SYSTEM.md (where they name tabs) describe Workflows | Actors | Variables | Statistics in step, keeping every load-bearing phrase the harness probes check
  - docs/specs/2026-10-03-culture-rules-engine-editor.md gets a dated amendment section (not silent edits); README.md, web/README.md, docs/demo.md, docs/operations/pr-fixer.md, docs/run-events.md describe the folded editor
  - harness-smoke --stage config, teken cli doctor --strict, culture-rules doctor and markdownlint over the tree pass

### t11 — Boundary check, version bump and PR

- instruction: t1 adds only tests/engine; anything under `culture_rules`/ in the diff is a stop.
- depends on: t1, t9, t10
- covers: c12, h7
- acceptance:
  - git diff origin/main touches no file under `culture_rules`/ and leaves api/openapi.json unchanged; tests/server/`test_openapi_contract.py` passes
  - minor version bump with a CHANGELOG entry; the full suite and every lint gate in CLAUDE.md pass; the PR opens through the cicd skill

### t12 — Live verification after rollout, including the D7 conversion

- instruction: Needs the operator's deploy go-ahead. Re-run the read-only run-event probe right before the D7 conversion.
- depends on: t11
- covers: c1, h13, c18, h15, c15
- acceptance:
  - on rules.culture.dev: no Rules tab; the PR fixer shows as 1 chain card, 4 workflows, 6 entry points from live rules; Simple, Detailed and Debug work on every workflow; an old /rules link redirects
  - the live action-only rule (test-jira-scrum21-to-discord) is converted through the editor's D7 offer after re-checking that no live run-event rule lacks a `workflow_id` filter; its next run behaves as before
  - results are recorded in the evidence log

## Deferred targets

- `c8` (why_it_matters): Rules and workflows describe one piece of automation; showing them apart hides what starts a workflow, what it continues into and how it ends, and a single canvas picture is too busy for reading the flow yet too thin for debugging data. — deferred: Context claim (why it matters), confirmed by the operator on canvas v11; nothing to build.
- `h3` (honesty): The operator confirmed the fold and the three views on the canvas (v11) because the split view hid what starts and continues a workflow. — deferred: Honesty condition on c8: already evidenced by the operator's canvas confirmation; nothing to build.

## Risks

- [unknown_nonblocking] t4, t6 and t9 may each extend the shared fake API (web/src/rules/fake-api.ts) for failing or concurrent writes; same-wave edits there would collide at merge (task t4)
