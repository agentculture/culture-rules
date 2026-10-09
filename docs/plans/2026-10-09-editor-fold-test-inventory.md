# Editor fold: pre-fold test inventory and before state

Task **t2** of the plan
[`editor-rules-folded-into-workflows-three-views`](2026-10-09-editor-rules-folded-into-workflows-three-views.md)
(spec: [`docs/specs/2026-10-09-editor-rules-folded-into-workflows-three-views.md`](../specs/2026-10-09-editor-rules-folded-into-workflows-three-views.md)).
It covers h12 (every pre-fold scenario maps to a folded test, none deleted without
one), h2 (the before state) and c7 (the Before → After claim).

This is a read-only survey. It was taken at `origin/main` 9d72b1e (culture-rules
0.16.1); `web/` and `docs/rules/` on this branch are byte-identical to it.

## Before state at main

### The PR fixer is 9 rules on `/rules` and 4 workflows on `/workflows`

The PR fixer's stored definitions are in `docs/rules/pr-fixer/`: **9 rules**
(`rules/*.json`) and **4 workflows** (`workflows/*.json`). In the editor at main
the 9 rules are rows of the **Rules** tab (`/rules/:ruleId?`, `web/src/routes/Rules.tsx`)
and the 4 workflows are rows of the **Workflows** tab (`/workflows?id=<id>`,
`web/src/routes/Workflows.tsx`). They are two of the five primary tabs
(`web/src/components/Header.tsx`: Rules, Workflows, Actors, Variables,
Statistics), and the catch-all route lands on `/rules` (`web/src/App.tsx`).

| Rule | Trigger (`params.type`) | Starts workflow | Continues from (condition `data.workflow_id ==`) |
|------|-------------------------|-----------------|---------------------------------------------------|
| `pr-fixer-checks` | `github.pr.checks_settled` | `pr-fix` | (entry point) |
| `pr-fixer-comment` | `github.comment.created` | `pr-fix` | (entry point) |
| `pr-fixer-review` | `github.review.submitted` | `pr-fix` | (entry point) |
| `pr-fixer-review-comment` | `github.review_comment.created` | `pr-fix` | (entry point) |
| `pr-fixer-secrets` | `github.pr.checks_settled` | `report-secrets` | (entry point) |
| `pr-fixer-secrets-late` | `github.pr.checks_failed_late` | `report-secrets` | (entry point) |
| `pr-fixer-review-commit` | `rules.run.succeeded` | `review-commit` | `pr-fix` |
| `pr-fixer-refix` | `rules.run.succeeded` | `pr-fix` | `review-commit` |
| `pr-fixer-publish` | `rules.run.succeeded` | `publish-fix` | `review-commit` |

| Workflow | Name | Steps | Started by (rules) |
|----------|------|-------|--------------------|
| `pr-fix` | PR fix | 5 | checks, comment, review, review-comment, refix |
| `review-commit` | Review the fix | 2 | review-commit |
| `publish-fix` | Publish the approved fix | 4 | publish |
| `report-secrets` | Report GitGuardian findings | 1 | secrets, secrets-late |

Read together, that is the shape the fold must show: 1 chain, 4 workflows,
6 entry points and 3 continuations. To see it at main you read 9 rules on one tab
and 4 workflows on another and join them in your head.

### No link from a workflow to its rules

At main there is no link from a workflow to its rules. Nothing on the Workflows
tab links a workflow to the rules that start it:

- `web/src/routes/Workflows.tsx` loads the rule list (`listRules` in
  `useWorkflowsLoad`) into its `Loaded` state, but nothing renders it. No
  component under `web/src/workflows/` or in `routes/Workflows.tsx` builds a
  `/rules` link or names a rule.
- `web/src/workflows/WorkflowList.tsx` uses `rule-row` / `rule-list` only as CSS
  class names shared with the Rules list. Its rows link to `/workflows?id=<id>`
  only.
- A rule is named on `/workflows` in one place only: the server's refusal text
  when a workflow still used by a rule is deleted (`workflows/<id> is used by a
  rule`, I036 below). It is plain text, not a link.
- The Rules tab's canvas shows a rule's workflow as a stage. It goes from rule
  to workflow only, never back.

## How to read the inventory

**Scope.** These rows are every vitest and Playwright scenario under `web/` that
addresses the Rules tab, rules or workflows, plus the webglass agent-state
checks:

- every scenario in the rules and workflows suites (`e2e/rules*.spec.ts`,
  `e2e/workflows*.spec.ts`, `src/routes/Rules*`, `src/routes/rules-view*`,
  `src/routes/Workflows*`, `src/rules/`, `src/workflows/`, `src/api/rules*`,
  `src/api/workflows*`);
- the shell suites that pin the Rules tab (`e2e/shell.spec.ts`,
  `src/App.test.tsx`) and the agent-state store (`src/agent-state/`);
- in the other suites, each scenario whose body opens `/rules` or `/workflows`,
  lists or links rules, or calls a rule or workflow endpoint.

The survey found 506 scenarios in 58 files under `web/src` and `web/e2e` (by
source declaration). **369** of them are in scope and listed below, one row each.

**Out of scope.** These scenarios mention a rule or workflow only incidentally,
so the fold does not touch them:

- Actors: the repo name `culture-rules`, and "the dotted rule" for event names.
- Statistics: run fixtures that carry `rule_id`, and the per-tab
  `#agent-state` checks of Actors and Statistics.
- `stages.test.tsx`: the label "Rule enabled" on a generic Switch.
- `settle.test.ts`: an error message that says "no such rule".
- Every other scenario: no rule or workflow at all.

**Rows.** One row per source declaration. Some rows run as several cases:

- `it.each` / `test.each` rows: I067 (App), I232 (RulesEditor), I306 and I307
  (StepEditor) expand into several cases at run time.
- Loop-generated Playwright rows: I001 (one test per entry of the `TABS` table,
  Rules first) and I003 (with one workflow and with none).

The **ID** is stable so t9 can refer to it. **File:line** is relative to `web/`.
**Describe › test** is the full name as the runner prints it.

**Fold impact** says what in the scenario is bound to today's split editor:

| Code | Rows | Meaning |
|------|------|---------|
| MOVE | 72 (+2 with STATE) | Mounts the Rules tab (`/rules`, `Rules.tsx`, a `/rules/<id>` link). The scenario moves to a workflow's Simple view entry point (t6), reached through `/workflows/<workflow>?entry=<rule>` or the old-link redirect (t8). |
| SHELL | 11 (+2 with STATE, +1 with LIST) | Asserts the five-tab shell, the Rules nav link, the catch-all landing on `/rules` or a Rules-tab layout. It is rewritten for Workflows, Actors, Variables and Statistics with `/workflows` as the landing (t8). |
| PICK | 48 | A rules component or helper that the Simple view imports unchanged (TriggerPicker, ActionPicker, relations, `slugFor`). It is expected to pass as is. t9 also needs it to pass as reached from the Simple view. |
| DETAIL | 100 (+1 with LIST) | Workflow canvas behaviour. Detailed renders today's canvas unchanged (t5), so the scenario stays. If Simple becomes the default mode, it must first switch to Detailed. |
| LIST | 49 (+3 combined) | The Workflows list pane (`WorkflowList`, New, rename, row switch, delete and restore rows). Its body becomes chain cards (t7). |
| API | 78 | API client or pure model, with no route or tab. No fold impact is expected, and it is kept as is. |
| STATE | 4 (+5 combined) | The agent-state contract. Tab `rules` and `AgentRulesState` stay as a deprecated alias for one release, and `workflows` carries the entry-point state (decision q8, t8). |

**Folded test (t9)** reads `pending t9` in every row. t9 replaces it with the
folded test that covers the scenario, as `file::test name`, or with `kept as is`
when the scenario passes unchanged under the fold. No row is deleted without a
replacement (h12).

## webglass agent-state checks (CI `web` job)

These run outside vitest and Playwright, on the built bundle in
`.github/workflows/tests.yml`. Tests in `tests/test_ci_workflow.py` pin them.

| ID | Where | Check | Fold impact | Folded test (t9) |
|----|-------|-------|-------------|------------------|
| G1 | `.github/workflows/tests.yml` step "webglass ready - page extract '#agent-state'" | Opens the preview at `/` and re-reads `#agent-state` until one match reports `"status": "ready"`. At main `/` redirects to `/rules`, so the gate samples the Rules tab's state. | STATE+SHELL: after the fold `/` lands on `/workflows`, so the gate samples the Workflows tab (entry-point state). The q8 alias keeps `rules` readable for one release. | pending t9 |
| G2 | `.github/workflows/tests.yml` step "webglass console lens (no uncaught page errors)" | Opens `/`, and requires `lifecycle_state == "succeeded"` and no `page_errors`. | SHELL: same landing change as G1. | pending t9 |
| G3 | `tests/test_ci_workflow.py::test_web_job_steps` | Requires the web job to run `webglass` and to check `#agent-state` and `ready`. | API: no fold impact expected. | pending t9 |

## Vitest and Playwright scenarios

### `web/e2e/hit-area.spec.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I001 | `e2e/hit-area.spec.ts:100` | d4: every control has a 44x44 hit area › ${tab.name} tab and the header | SHELL: loops over a TABS table whose first entry is Rules at /rules/build-and-publish | pending t9 |
| I002 | `e2e/hit-area.spec.ts:108` | d4: every control has a 44x44 hit area › Workflows tab: the New workflow name form | LIST | pending t9 |
| I003 | `e2e/hit-area.spec.ts:122` | d4: every control has a 44x44 hit area › Workflows tab: the list's rows and New button (${label}) | LIST | pending t9 |

### `web/e2e/live.spec.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I004 | `e2e/live.spec.ts:22` | live editor updates (h61 / c80) › a rule toggled in one browser shows in another without a reload | MOVE | pending t9 |
| I005 | `e2e/live.spec.ts:58` | live editor updates (h61 / c80) › a skipped (superseded) rule reads 'superseded by &lt;rule&gt;' in Last runs | MOVE | pending t9 |

### `web/e2e/rules-pickers.spec.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I006 | `e2e/rules-pickers.spec.ts:37` | Trigger picker › app surface then a declared event writes trigger.params.type; no free-text event field | MOVE | pending t9 |
| I007 | `e2e/rules-pickers.spec.ts:55` | Trigger picker › an event trigger without an event is refused with a guided notice and nothing is sent | MOVE | pending t9 |
| I008 | `e2e/rules-pickers.spec.ts:62` | Trigger picker › a schedule preset writes its cron | MOVE | pending t9 |
| I009 | `e2e/rules-pickers.spec.ts:73` | Trigger picker › a probe writes actor, command, mode and schedule | MOVE | pending t9 |
| I010 | `e2e/rules-pickers.spec.ts:91` | Action picker › github.comment with an actor and number mapped to trigger.data.number shows a chip and saves the ref | MOVE | pending t9 |
| I011 | `e2e/rules-pickers.spec.ts:120` | Action picker › an incomplete action is refused with a guided notice | MOVE | pending t9 |

### `web/e2e/rules.spec.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I012 | `e2e/rules.spec.ts:19` | Rules tab › the focused rule reads must-after ghost → trigger → condition → workflow → action → + | MOVE | pending t9 |
| I013 | `e2e/rules.spec.ts:48` | Rules tab › (i) on a list row and on the rule: the description, keyboard, focus return, no navigation (d19) | MOVE | pending t9 |
| I014 | `e2e/rules.spec.ts:75` | Rules tab › toggle, edit and delete-with-undo work and reach the API | MOVE | pending t9 |
| I015 | `e2e/rules.spec.ts:101` | Rules tab › a relationship is made by dragging a rule onto a slot, shows on both ends, and is removed | MOVE | pending t9 |
| I016 | `e2e/rules.spec.ts:132` | Rules tab › must-after shows as a badge on both rules and is editable from either end | MOVE | pending t9 |
| I017 | `e2e/rules.spec.ts:145` | Rules tab › a pending human ask is answered in context | MOVE | pending t9 |
| I018 | `e2e/rules.spec.ts:157` | Rules tab › keyboard: Space toggles a switch, the picker adds a relationship, Escape closes the editor | MOVE | pending t9 |
| I019 | `e2e/rules.spec.ts:177` | Rules tab › a new rule starts from 'New rule', asks 'When does this happen?' and grows through + | MOVE | pending t9 |
| I020 | `e2e/rules.spec.ts:196` | Rules tab › axe: no serious or critical violations while editing, with asks and relationships | MOVE+STATE: axe run waits on #agent-state of the Rules tab | pending t9 |
| I021 | `e2e/rules.spec.ts:208` | Rules tab › prefers-reduced-motion disables the editor's transitions | MOVE | pending t9 |

### `web/e2e/shell.spec.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I022 | `e2e/shell.spec.ts:17` | culture-rules shell › exactly five top-level tabs and no runs/history route | SHELL | pending t9 |
| I023 | `e2e/shell.spec.ts:33` | culture-rules shell › #agent-state reports ready with no errors; identity from GET /whoami | SHELL+STATE: opens /rules/build-and-publish and reads #agent-state | pending t9 |
| I024 | `e2e/shell.spec.ts:57` | culture-rules shell › keyboard walk: Tab reaches every tab and Enter switches to it | SHELL | pending t9 |
| I025 | `e2e/shell.spec.ts:85` | culture-rules shell › axe: no serious or critical violations on any tab | SHELL | pending t9 |
| I026 | `e2e/shell.spec.ts:98` | culture-rules shell › prefers-reduced-motion disables transitions | SHELL | pending t9 |
| I027 | `e2e/shell.spec.ts:112` | culture-rules shell › Rules tab matches the 'Chosen — Rules' board layout (screenshot) | SHELL | pending t9 |

### `web/e2e/variables.spec.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I028 | `e2e/variables.spec.ts:12` | Variables tab › lists variables with their history and the rules that use them | MOVE: "Used by rules" links point at /rules/&lt;id&gt;; they must point at the entry point | pending t9 |
| I029 | `e2e/variables.spec.ts:58` | Condition editor: variable picker › an author check picks vars.trusted_authors and saves a var operand | MOVE | pending t9 |

### `web/e2e/workflows-create.spec.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I030 | `e2e/workflows-create.spec.ts:46` | Workflows tab: New workflow › empty state: the primary action asks for a name, Enter creates and opens the canvas | LIST | pending t9 |
| I031 | `e2e/workflows-create.spec.ts:98` | Workflows tab: New workflow › Escape closes the name form without writing | LIST | pending t9 |
| I032 | `e2e/workflows-create.spec.ts:110` | Workflows tab: New workflow › with a workflow selected, the list's New button creates and switches to the new one | LIST | pending t9 |
| I033 | `e2e/workflows-create.spec.ts:141` | Workflows tab: New workflow › an API error shows inline in the form | LIST | pending t9 |
| I034 | `e2e/workflows-create.spec.ts:165` | Workflows tab: rename, enable / disable and delete › rename edits the draft name; Save PUTs it | LIST | pending t9 |
| I035 | `e2e/workflows-create.spec.ts:183` | Workflows tab: rename, enable / disable and delete › the list row's switch disables and enables the stored workflow | LIST | pending t9 |
| I036 | `e2e/workflows-create.spec.ts:198` | Workflows tab: rename, enable / disable and delete › delete is soft, with Undo; a workflow a rule uses is kept and the conflict named | LIST: delete conflict names the rule that uses the workflow | pending t9 |

### `web/e2e/workflows-flows.spec.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I037 | `e2e/workflows-flows.spec.ts:41` | Workflows: Run form › Run opens a typed form; submitting POSTs typed inputs, overlays the run and shows its outputs | DETAIL | pending t9 |
| I038 | `e2e/workflows-flows.spec.ts:74` | Workflows: Run form › Escape closes the form without sending | DETAIL | pending t9 |
| I039 | `e2e/workflows-flows.spec.ts:85` | Workflows: step panel › an invalid timeout is a guided error that blocks Done; a valid one is saved | DETAIL | pending t9 |
| I040 | `e2e/workflows-flows.spec.ts:119` | Workflows: inputs and outputs editors › the in node opens the inputs editor on click and Enter; an input can be added and saved | DETAIL | pending t9 |
| I041 | `e2e/workflows-flows.spec.ts:148` | Workflows: inputs and outputs editors › the out node opens the outputs and variables editor | DETAIL | pending t9 |
| I042 | `e2e/workflows-flows.spec.ts:165` | Workflows: deleted and purge › an admin sees Purge; it dry-runs, then Confirm purge sends {apply:true} | DETAIL | pending t9 |
| I043 | `e2e/workflows-flows.spec.ts:187` | Workflows: deleted and purge › Cancel leaves the workflow unpurged | DETAIL | pending t9 |
| I044 | `e2e/workflows-flows.spec.ts:196` | Workflows: deleted and purge › a non-admin sees the deleted workflow and Restore, but no Purge | DETAIL | pending t9 |

### `web/e2e/workflows-list.spec.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I045 | `e2e/workflows-list.spec.ts:48` | Workflows tab: the workflow list › sits left of the board, aligned with the Rules list (screenshots) | LIST+SHELL: screenshots the list aligned with the Rules list on /rules | pending t9 |
| I046 | `e2e/workflows-list.spec.ts:110` | Workflows tab: the workflow list › shows with one workflow (screenshot) and with none | LIST | pending t9 |
| I047 | `e2e/workflows-list.spec.ts:125` | Workflows tab: the workflow list › a row opens its workflow; its switch enables / disables it | LIST | pending t9 |
| I048 | `e2e/workflows-list.spec.ts:145` | Workflows tab: the workflow list › keyboard: Tab reaches every row, Enter opens it, Escape still closes the name form | LIST | pending t9 |
| I049 | `e2e/workflows-list.spec.ts:171` | Workflows tab: the workflow list › axe: no serious or critical violations on the board with the list | LIST | pending t9 |

### `web/e2e/workflows.spec.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I050 | `e2e/workflows.spec.ts:30` | Workflows tab › matches the 'Chosen — Workflows' board layout (screenshot) | DETAIL | pending t9 |
| I051 | `e2e/workflows.spec.ts:91` | Workflows tab › edits a step's placement, enable switch and saves with PUT | DETAIL | pending t9 |
| I052 | `e2e/workflows.spec.ts:118` | Workflows tab › typed ports: a drag between mismatched types is refused, a matching one wires | DETAIL | pending t9 |
| I053 | `e2e/workflows.spec.ts:135` | Workflows tab › deletes a step and adds one | DETAIL | pending t9 |
| I054 | `e2e/workflows.spec.ts:146` | Workflows tab › Import, Export and the repo picker call the io endpoints | DETAIL | pending t9 |
| I055 | `e2e/workflows.spec.ts:195` | Workflows tab › a run lights its path with each step's host and outcome (persisted run state) | DETAIL | pending t9 |
| I056 | `e2e/workflows.spec.ts:211` | Workflows tab › keyboard: a step is reachable, Enter selects it, its toolbar is operable | DETAIL | pending t9 |
| I057 | `e2e/workflows.spec.ts:224` | Workflows tab › axe: no serious or critical violations, also with a run and an open editor | DETAIL | pending t9 |
| I058 | `e2e/workflows.spec.ts:238` | Workflows tab › zoom (d19): buttons, keys and ctrl + wheel zoom; a plain wheel scrolls the page | DETAIL | pending t9 |
| I059 | `e2e/workflows.spec.ts:281` | Workflows tab › phone width: the zoom row and the step + do not overlap and both take clicks (d19) | DETAIL | pending t9 |
| I060 | `e2e/workflows.spec.ts:316` | Workflows tab › Fit after narrowing the window fits the new width (d19) | DETAIL | pending t9 |
| I061 | `e2e/workflows.spec.ts:330` | Workflows tab › (i) on the head and the list row: mouse and keyboard, the API's lines, focus return (d19) | DETAIL+LIST: (i) on the canvas head and on the list row | pending t9 |
| I062 | `e2e/workflows.spec.ts:366` | Workflows tab › (i) panels stay inside the window beside a long description (d19) | DETAIL | pending t9 |
| I063 | `e2e/workflows.spec.ts:420` | Workflows tab › (i) docks to the bottom of the screen at phone width (d19) | DETAIL | pending t9 |
| I064 | `e2e/workflows.spec.ts:431` | Workflows tab › prefers-reduced-motion disables the canvas transitions | DETAIL | pending t9 |

### `web/src/App.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I065 | `src/App.test.tsx:25` | App shell › has exactly five top-level tabs, in order | SHELL | pending t9 |
| I066 | `src/App.test.tsx:38` | App shell › marks the current tab with aria-current=page | SHELL | pending t9 |
| I067 | `src/App.test.tsx:48` | App shell › has no top-level %s route: it lands on Rules | SHELL | pending t9 |
| I068 | `src/App.test.tsx:62` | App shell › renders exactly one #agent-state node and reports the tab | SHELL+STATE: one #agent-state node reporting the tab | pending t9 |
| I069 | `src/App.test.tsx:68` | App shell › names the credential kind on the avatar for a service identity | SHELL | pending t9 |
| I070 | `src/App.test.tsx:79` | App shell › shows who is signed in, from GET /whoami | SHELL | pending t9 |

### `web/src/agent-state/store.test.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I071 | `src/agent-state/store.test.ts:13` | agent-state store › starts loading with no errors | STATE | pending t9 |
| I072 | `src/agent-state/store.test.ts:17` | agent-state store › does not notify when nothing changed | STATE | pending t9 |
| I073 | `src/agent-state/store.test.ts:26` | agent-state store › derives ready from the view AND identity having loaded | STATE | pending t9 |
| I074 | `src/agent-state/store.test.ts:33` | agent-state store › escapes &lt; so API data cannot close the script element | STATE | pending t9 |

### `web/src/api/client.test.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I075 | `src/api/client.test.ts:21` | api client › unwraps the {items} list envelope | API | pending t9 |
| I076 | `src/api/client.test.ts:26` | api client › passes run filters as a query string | API | pending t9 |
| I077 | `src/api/client.test.ts:32` | api client › passes the workflow and host run filters | API | pending t9 |
| I078 | `src/api/client.test.ts:38` | api client › exports the shared request helpers the tab adapters build on | API | pending t9 |
| I079 | `src/api/client.test.ts:49` | api client › surfaces the error envelope's code and message | API | pending t9 |
| I080 | `src/api/client.test.ts:61` | api client › never attaches a credential header | API | pending t9 |
| I081 | `src/api/client.test.ts:69` | api client › turns a request that never answers into a timeout error, so a view still settles | API | pending t9 |
| I082 | `src/api/client.test.ts:92` | api client › still honours a caller's own abort signal | API | pending t9 |

### `web/src/api/guidance.test.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I083 | `src/api/guidance.test.ts:17` | guidance table › covers the planned and server vocabularies | API: rule and workflow error codes | pending t9 |

### `web/src/api/live.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I084 | `src/api/live.test.tsx:52` | useLiveUpdates › is off under tests unless an EventSource factory is injected | API | pending t9 |
| I085 | `src/api/live.test.tsx:59` | useLiveUpdates › subscribes to /api/events/stream for the named collections | API | pending t9 |
| I086 | `src/api/live.test.tsx:68` | useLiveUpdates › hands a batch of change events to onChange, coalesced | API | pending t9 |
| I087 | `src/api/live.test.tsx:88` | useLiveUpdates › reconnects after a fatal error, resuming after the last event id | API | pending t9 |
| I088 | `src/api/live.test.tsx:108` | useLiveUpdates › leaves a retrying (non-fatal) error to the browser's own reconnect | API | pending t9 |
| I089 | `src/api/live.test.tsx:118` | useLiveUpdates › closes the stream on unmount | API | pending t9 |
| I090 | `src/api/live.test.tsx:125` | useLiveUpdates › flashes on a change, but never under prefers-reduced-motion | API | pending t9 |

### `web/src/api/rules.test.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I091 | `src/api/rules.test.ts:11` | rules API: asks › GET /asks?run_id=&status=open lists the run's open asks | API | pending t9 |
| I092 | `src/api/rules.test.ts:17` | rules API: asks › a missing route is an error now that the API serves /asks (no 404 fallback) | API | pending t9 |

### `web/src/api/workflows.run.test.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I093 | `src/api/workflows.run.test.ts:12` | workflows API: run, purge, include_deleted › POST /workflows/{id}/run sends {inputs} and answers the run doc | API | pending t9 |
| I094 | `src/api/workflows.run.test.ts:21` | workflows API: run, purge, include_deleted › runWorkflow defaults to empty inputs | API | pending t9 |
| I095 | `src/api/workflows.run.test.ts:27` | workflows API: run, purge, include_deleted › a 422 invalid_inputs is an ApiError with its code | API | pending t9 |
| I096 | `src/api/workflows.run.test.ts:38` | workflows API: run, purge, include_deleted › POST /workflows/{id}/purge sends {apply} — dry-run unless applied | API | pending t9 |
| I097 | `src/api/workflows.run.test.ts:50` | workflows API: run, purge, include_deleted › GET /workflows?include_deleted=true only when asked | API | pending t9 |

### `web/src/api/workflows.test.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I098 | `src/api/workflows.test.ts:22` | workflows API client › GET /export?format=json | API | pending t9 |
| I099 | `src/api/workflows.test.ts:29` | workflows API client › POST /import sends {files, apply} — dry-run unless applied | API | pending t9 |
| I100 | `src/api/workflows.test.ts:40` | workflows API client › POST /export writes into a repo — a dry-run plan unless applied | API | pending t9 |
| I101 | `src/api/workflows.test.ts:49` | workflows API client › POST /import with a repo reads the definitions from it | API | pending t9 |
| I102 | `src/api/workflows.test.ts:55` | workflows API client › GET /repos lists repositories | API | pending t9 |
| I103 | `src/api/workflows.test.ts:61` | workflows API client › PUT /workflows/{id} replaces a definition | API | pending t9 |
| I104 | `src/api/workflows.test.ts:68` | workflows API client › runs: list filtered to one workflow, one by id, start through a rule | API | pending t9 |
| I105 | `src/api/workflows.test.ts:82` | workflows API client › an error envelope becomes an ApiError with its message | API | pending t9 |

### `web/src/components/AboutButton.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I106 | `src/components/AboutButton.test.tsx:46` | AboutButton (d19) › a click opens the panel with the API's lines verbatim (indentation and symbols kept) | API | pending t9 |
| I107 | `src/components/AboutButton.test.tsx:105` | AboutButton (d19) › names a failed call, and notes the saved version for a dirty draft | API | pending t9 |
| I108 | `src/components/AboutButton.test.tsx:117` | AboutButton (d19) › reopening keeps the last description (same Close) until the fresh one lands: no blink | API | pending t9 |
| I109 | `src/components/AboutButton.test.tsx:160` | AboutButton (d19) › a different id never shows the previous one's lines | API: rules describe; reused per entry point (o17) | pending t9 |

### `web/src/routes/Rules.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I110 | `src/routes/Rules.test.tsx:31` | Rules board (Chosen — Rules) › the rule's (i), in the head and on its list row, opens GET /rules/{id}/describe (d19) | MOVE: rule describe (o17: per entry point) | pending t9 |
| I111 | `src/routes/Rules.test.tsx:57` | Rules board (Chosen — Rules) › lists every rule with an enable switch, the first affordance a 'New rule' button | MOVE | pending t9 |
| I112 | `src/routes/Rules.test.tsx:72` | Rules board (Chosen — Rules) › draws the selected rule as Trigger → Condition → Workflow → Action stages | MOVE | pending t9 |
| I113 | `src/routes/Rules.test.tsx:91` | Rules board (Chosen — Rules) › shows a relationship as a dashed card, not a stage | MOVE | pending t9 |
| I114 | `src/routes/Rules.test.tsx:99` | Rules board (Chosen — Rules) › shows placement with the machine's color | MOVE | pending t9 |
| I115 | `src/routes/Rules.test.tsx:105` | Rules board (Chosen — Rules) › lists the last runs contextually, with failure as icon+word | MOVE | pending t9 |
| I116 | `src/routes/Rules.test.tsx:112` | Rules board (Chosen — Rules) › reports ready in agent-state with the stages it drew | MOVE+STATE: agent-state tab rules / AgentRulesState | pending t9 |
| I117 | `src/routes/Rules.test.tsx:123` | Rules board (Chosen — Rules) › selects the first rule when none is named | MOVE | pending t9 |
| I118 | `src/routes/Rules.test.tsx:128` | Rules board (Chosen — Rules) › names a failure that has no string form and still reports ready | MOVE | pending t9 |
| I119 | `src/routes/Rules.test.tsx:137` | Rules board (Chosen — Rules) › names a load failure and still reports ready | MOVE | pending t9 |

### `web/src/routes/Variables.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I120 | `src/routes/Variables.test.tsx:49` | Variables tab › shows the selected variable's value, version history and the rules that reference it | MOVE: "Used by rules" links point at /rules/&lt;id&gt; | pending t9 |
| I121 | `src/routes/Variables.test.tsx:237` | lossless editing › after switching variables the old one's history and rules are not shown while the new load is pending | MOVE: the "Used by rules" list while switching variables | pending t9 |

### `web/src/routes/Workflows.deleted.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I122 | `src/routes/Workflows.deleted.test.tsx:81` | Workflows: soft-deleted view and admin purge › hides deleted workflows by default and toggles include_deleted | LIST | pending t9 |
| I123 | `src/routes/Workflows.deleted.test.tsx:97` | Workflows: soft-deleted view and admin purge › never shows Purge to a non-admin, even with deleted workflows shown | LIST | pending t9 |
| I124 | `src/routes/Workflows.deleted.test.tsx:104` | Workflows: soft-deleted view and admin purge › shows Purge to an admin on deleted rows only | LIST | pending t9 |
| I125 | `src/routes/Workflows.deleted.test.tsx:111` | Workflows: soft-deleted view and admin purge › restores a deleted workflow from the list | LIST | pending t9 |
| I126 | `src/routes/Workflows.deleted.test.tsx:119` | Workflows: soft-deleted view and admin purge › purges only after a dry run and an explicit confirmation | LIST | pending t9 |
| I127 | `src/routes/Workflows.deleted.test.tsx:130` | Workflows: soft-deleted view and admin purge › cancelling the confirmation never applies the purge | LIST | pending t9 |
| I128 | `src/routes/Workflows.deleted.test.tsx:138` | Workflows: soft-deleted view and admin purge › explains a rule_referenced refusal in guided words | LIST | pending t9 |

### `web/src/routes/Workflows.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I129 | `src/routes/Workflows.test.tsx:106` | Workflows board (Chosen — Workflows) › names a load that fails while being applied and still reports ready | DETAIL | pending t9 |
| I130 | `src/routes/Workflows.test.tsx:114` | Workflows board (Chosen — Workflows) › heads the board with the workflow name, version and the io controls | DETAIL | pending t9 |
| I131 | `src/routes/Workflows.test.tsx:126` | Workflows board (Chosen — Workflows) › draws inputs, every step with its machine and enable switch, and outputs | DETAIL | pending t9 |
| I132 | `src/routes/Workflows.test.tsx:147` | Workflows board (Chosen — Workflows) › toggling a step marks the workflow dirty and Save PUTs only schema fields | DETAIL | pending t9 |
| I133 | `src/routes/Workflows.test.tsx:165` | Workflows board (Chosen — Workflows) › selecting a step shows its toolbar; placement switches machine \| actor \| requirement | DETAIL | pending t9 |
| I134 | `src/routes/Workflows.test.tsx:193` | Workflows board (Chosen — Workflows) › deletes a step from its toolbar and adds one with + | DETAIL | pending t9 |
| I135 | `src/routes/Workflows.test.tsx:205` | Workflows board (Chosen — Workflows) › the step editor edits typed ports and wires inputs to compatible sources only | DETAIL | pending t9 |
| I136 | `src/routes/Workflows.test.tsx:222` | Workflows board (Chosen — Workflows) › Export calls GET /export and offers the bundle as a download | DETAIL | pending t9 |
| I137 | `src/routes/Workflows.test.tsx:238` | Workflows board (Chosen — Workflows) › Import posts the chosen files as a dry run, then applies the plan | DETAIL | pending t9 |
| I138 | `src/routes/Workflows.test.tsx:256` | Workflows board (Chosen — Workflows) › the repo picker lists repositories from GET /repos | DETAIL | pending t9 |
| I139 | `src/routes/Workflows.test.tsx:268` | Workflows board (Chosen — Workflows) › the repo picker exports to and imports from the chosen repository: dry-run, then apply | DETAIL | pending t9 |
| I140 | `src/routes/Workflows.test.tsx:317` | Workflows board (Chosen — Workflows) › a run lights each step with its host and outcome from GET /runs/{id} | DETAIL | pending t9 |
| I141 | `src/routes/Workflows.test.tsx:329` | Workflows board (Chosen — Workflows) › Run opens a typed form, starts a direct run, overlays it and shows outputs when it completes | DETAIL | pending t9 |
| I142 | `src/routes/Workflows.test.tsx:362` | Workflows board (Chosen — Workflows) › Run is disabled while the workflow is disabled | DETAIL | pending t9 |
| I143 | `src/routes/Workflows.test.tsx:370` | Workflows board (Chosen — Workflows) › the list pane opens a workflow via ?id= and agent-state reports the tab | LIST+STATE: list pane ?id= and agent-state tab workflows | pending t9 |
| I144 | `src/routes/Workflows.test.tsx:394` | Workflows board (Chosen — Workflows) › opening a row drops the overlaid run from the query | LIST | pending t9 |
| I145 | `src/routes/Workflows.test.tsx:404` | Workflows board (Chosen — Workflows) › a failed load is an alert, and the tab still settles ready | DETAIL | pending t9 |
| I146 | `src/routes/Workflows.test.tsx:436` | Workflows tab live updates (h61 / c80) › subscribes to workflows and runs | DETAIL | pending t9 |
| I147 | `src/routes/Workflows.test.tsx:443` | Workflows tab live updates (h61 / c80) › a runs change re-reads the overlaid run, so the overlay follows it | DETAIL | pending t9 |
| I148 | `src/routes/Workflows.test.tsx:461` | Workflows tab live updates (h61 / c80) › a workflows change elsewhere refetches the list without a reload | LIST: list refetch on a workflows change | pending t9 |
| I149 | `src/routes/Workflows.test.tsx:493` | Workflows: the in / out nodes and the empty canvas (t41) › clicking the in node opens the inputs editor; Escape closes it and gives focus back | DETAIL | pending t9 |
| I150 | `src/routes/Workflows.test.tsx:507` | Workflows: the in / out nodes and the empty canvas (t41) › pressing Enter on the in node opens the inputs editor | DETAIL | pending t9 |
| I151 | `src/routes/Workflows.test.tsx:517` | Workflows: the in / out nodes and the empty canvas (t41) › re-renders do not re-run the canvas ref (no update loop when the width keeps changing) | DETAIL | pending t9 |
| I152 | `src/routes/Workflows.test.tsx:542` | Workflows: the in / out nodes and the empty canvas (t41) › the out node (click or Enter) opens the outputs and variables editor | DETAIL | pending t9 |
| I153 | `src/routes/Workflows.test.tsx:557` | Workflows: the in / out nodes and the empty canvas (t41) › io edits go through the draft and Save PUTs them in schema shape | DETAIL | pending t9 |
| I154 | `src/routes/Workflows.test.tsx:596` | Workflows: the in / out nodes and the empty canvas (t41) › an empty workflow shows next-step guidance whose buttons add a step and open the inputs editor | DETAIL | pending t9 |

### `web/src/routes/WorkflowsCreate.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I155 | `src/routes/WorkflowsCreate.test.tsx:138` | Workflows tab: New workflow (empty state) › offers New workflow atop the (empty) list and as the empty state's primary action | LIST | pending t9 |
| I156 | `src/routes/WorkflowsCreate.test.tsx:154` | Workflows tab: New workflow (empty state) › asks only for a name, focuses it, and Escape closes the form | LIST | pending t9 |
| I157 | `src/routes/WorkflowsCreate.test.tsx:170` | Workflows tab: New workflow (empty state) › Enter creates a minimal workflow via POST /workflows and opens it on the canvas | LIST | pending t9 |
| I158 | `src/routes/WorkflowsCreate.test.tsx:201` | Workflows tab: New workflow (empty state) › shows an API validation error inline and keeps the form open | LIST | pending t9 |
| I159 | `src/routes/WorkflowsCreate.test.tsx:218` | Workflows tab: New workflow (empty state) › a blank name posts nothing | LIST | pending t9 |
| I160 | `src/routes/WorkflowsCreate.test.tsx:229` | Workflows tab: New workflow (empty state) › an id held by a deleted workflow (409) moves on to the next free id | LIST | pending t9 |
| I161 | `src/routes/WorkflowsCreate.test.tsx:247` | Workflows tab: New workflow with a workflow selected › the list's New button switches to the new workflow, which joins the list | LIST | pending t9 |
| I162 | `src/routes/WorkflowsCreate.test.tsx:273` | Workflows tab: New workflow with a workflow selected › Cancel returns to the selected workflow | LIST | pending t9 |
| I163 | `src/routes/WorkflowsCreate.test.tsx:284` | Workflows tab: New workflow with a workflow selected › opening a row from the list closes the name form | LIST | pending t9 |
| I164 | `src/routes/WorkflowsCreate.test.tsx:300` | Workflows tab: rename (update) › renames the draft; Save PUTs the new name | LIST | pending t9 |
| I165 | `src/routes/WorkflowsCreate.test.tsx:325` | Workflows tab: rename (update) › Escape leaves the name alone and returns focus to Rename | LIST | pending t9 |
| I166 | `src/routes/WorkflowsCreate.test.tsx:342` | Workflows tab: enable / disable and delete (parity with Rules) › the list row's switch posts disable then enable; the header has none (as Rules) | LIST: "parity with Rules": its wording names the Rules tab | pending t9 |
| I167 | `src/routes/WorkflowsCreate.test.tsx:363` | Workflows tab: enable / disable and delete (parity with Rules) › a row's switch toggles that workflow, not only the selected one | LIST | pending t9 |
| I168 | `src/routes/WorkflowsCreate.test.tsx:379` | Workflows tab: enable / disable and delete (parity with Rules) › Delete soft-deletes, moves on, and Undo restores | LIST | pending t9 |
| I169 | `src/routes/WorkflowsCreate.test.tsx:401` | Workflows tab: enable / disable and delete (parity with Rules) › deleting a workflow a rule still uses names the conflict and keeps it | LIST | pending t9 |
| I170 | `src/routes/WorkflowsCreate.test.tsx:412` | Workflows tab: enable / disable and delete (parity with Rules) › deleting the last workflow lands on the empty state | LIST | pending t9 |
| I171 | `src/routes/WorkflowsCreate.test.tsx:428` | Workflows toggle in flight (#7) › a double click while the toggle is in flight sends one request | LIST | pending t9 |

### `web/src/routes/WorkflowsZoomDescribe.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I172 | `src/routes/WorkflowsZoomDescribe.test.tsx:72` | Workflows: zoom and the (i) description (d19) › zoom out / zoom in / fit buttons are labelled, change the zoom and stop at the bounds | DETAIL | pending t9 |
| I173 | `src/routes/WorkflowsZoomDescribe.test.tsx:95` | Workflows: zoom and the (i) description (d19) › Fit uses the canvas's current width after a resize (not the width at mount) | DETAIL | pending t9 |
| I174 | `src/routes/WorkflowsZoomDescribe.test.tsx:139` | Workflows: zoom and the (i) description (d19) › + / - / 0 zoom while focus is in the canvas, but not while typing | DETAIL | pending t9 |
| I175 | `src/routes/WorkflowsZoomDescribe.test.tsx:160` | Workflows: zoom and the (i) description (d19) › cmd + wheel zooms; a plain wheel is left to scroll the page | DETAIL | pending t9 |
| I176 | `src/routes/WorkflowsZoomDescribe.test.tsx:173` | Workflows: zoom and the (i) description (d19) › the canvas grows and shrinks with the zoom | DETAIL | pending t9 |
| I177 | `src/routes/WorkflowsZoomDescribe.test.tsx:184` | Workflows: zoom and the (i) description (d19) › the head's (i) opens the workflow's description from GET /workflows/{id}/describe | DETAIL | pending t9 |
| I178 | `src/routes/WorkflowsZoomDescribe.test.tsx:198` | Workflows: zoom and the (i) description (d19) › with unsaved edits the head's panel says it is the saved version | DETAIL | pending t9 |
| I179 | `src/routes/WorkflowsZoomDescribe.test.tsx:212` | Workflows: zoom and the (i) description (d19) › the list row's (i) opens the same description without leaving the open workflow | LIST: the list row (i) | pending t9 |

### `web/src/routes/rules-view.test.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I180 | `src/routes/rules-view.test.ts:5` | slugFor › lower-cases and dashes a name | PICK | pending t9 |
| I181 | `src/routes/rules-view.test.ts:8` | slugFor › drops leading and trailing separators | PICK | pending t9 |
| I182 | `src/routes/rules-view.test.ts:11` | slugFor › falls back to `rule` when nothing is left | PICK | pending t9 |
| I183 | `src/routes/rules-view.test.ts:14` | slugFor › falls back to the given word when nothing is left | PICK | pending t9 |
| I184 | `src/routes/rules-view.test.ts:17` | slugFor › is unique among taken ids | PICK | pending t9 |
| I185 | `src/routes/rules-view.test.ts:20` | slugFor › stays fast on a long adversarial name | PICK | pending t9 |
| I186 | `src/routes/rules-view.test.ts:28` | trimDashes › drops dashes at both ends only | PICK | pending t9 |
| I187 | `src/routes/rules-view.test.ts:34` | trimDashes › is linear on a long inner run of dashes | PICK | pending t9 |

### `web/src/rules/ActionPicker.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I188 | `src/rules/ActionPicker.test.tsx:28` | kinds offered › offers only kinds some enabled actor supports, plus the mesh message and noop | PICK | pending t9 |
| I189 | `src/rules/ActionPicker.test.tsx:35` | kinds offered › offers http.call once an enabled runner allows a host | PICK | pending t9 |
| I190 | `src/rules/ActionPicker.test.tsx:48` | actor select › lists only the actors that support the picked kind | PICK | pending t9 |
| I191 | `src/rules/ActionPicker.test.tsx:61` | typed params and mappings › saves a mapped param as the reference string and renders a chip showing it | PICK | pending t9 |
| I192 | `src/rules/ActionPicker.test.tsx:83` | typed params and mappings › offers workflow outputs of the rule's workflow and a custom path | PICK | pending t9 |
| I193 | `src/rules/ActionPicker.test.tsx:98` | typed params and mappings › writes a machine.command's command and a typed args mapping | PICK | pending t9 |
| I194 | `src/rules/ActionPicker.test.tsx:115` | validation › names a missing actor or required param by code | PICK | pending t9 |
| I195 | `src/rules/ActionPicker.test.tsx:128` | existing action › preselects the kind, actor, literals and mapped chips | PICK | pending t9 |
| I196 | `src/rules/ActionPicker.test.tsx:145` | existing action › keeps an action kind the editor does not know as it is | PICK | pending t9 |
| I197 | `src/rules/ActionPicker.test.tsx:198` | mesh message and Discord message are separate kinds › lists both kinds with their own labels; the mesh message has no actor | PICK | pending t9 |
| I198 | `src/rules/ActionPicker.test.tsx:213` | mesh message and Discord message are separate kinds › picks the server and then the channel from what the bot can see | PICK | pending t9 |
| I199 | `src/rules/ActionPicker.test.tsx:248` | mesh message and Discord message are separate kinds › preselects the only server | PICK | pending t9 |
| I200 | `src/rules/ActionPicker.test.tsx:257` | mesh message and Discord message are separate kinds › maps the channel from the trigger to reply where the message came from | PICK | pending t9 |
| I201 | `src/rules/ActionPicker.test.tsx:268` | mesh message and Discord message are separate kinds › shows a stored message through a Discord actor as a Discord message | PICK | pending t9 |
| I202 | `src/rules/ActionPicker.test.tsx:279` | mesh message and Discord message are separate kinds › falls back to typing the channel id when the bot's channels cannot be loaded | PICK | pending t9 |
| I203 | `src/rules/ActionPicker.test.tsx:316` | github.push and github.review_reply › offers both once an app declares them | PICK | pending t9 |
| I204 | `src/rules/ActionPicker.test.tsx:325` | github.push and github.review_reply › requires the push's params and saves them typed | PICK | pending t9 |
| I205 | `src/rules/ActionPicker.test.tsx:342` | github.push and github.review_reply › saves a review reply with a boolean resolve | PICK | pending t9 |
| I206 | `src/rules/ActionPicker.test.tsx:358` | github.push and github.review_reply › rejects a non-boolean resolve | PICK | pending t9 |
| I207 | `src/rules/ActionPicker.test.tsx:369` | only where its chain ends (d21) › toggles the flag and keeps it while params change | PICK | pending t9 |
| I208 | `src/rules/ActionPicker.test.tsx:383` | only where its chain ends (d21) › shows a stored flag checked | PICK | pending t9 |

### `web/src/rules/RulesEditor.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I209 | `src/rules/RulesEditor.test.tsx:33` | toggle › enables a disabled rule from the list and keeps it enabled | MOVE | pending t9 |
| I210 | `src/rules/RulesEditor.test.tsx:44` | toggle › disables with POST /disable, and rolls back with an alert when the API refuses | MOVE | pending t9 |
| I211 | `src/rules/RulesEditor.test.tsx:63` | edit › edits name, trigger, action and placement and saves with PUT | MOVE | pending t9 |
| I212 | `src/rules/RulesEditor.test.tsx:90` | edit › lands keyboard focus in the first field of every form it opens | MOVE | pending t9 |
| I213 | `src/rules/RulesEditor.test.tsx:120` | edit › Escape cancels without sending anything | MOVE | pending t9 |
| I214 | `src/rules/RulesEditor.test.tsx:129` | edit › names a refused save and keeps the form open | MOVE | pending t9 |
| I215 | `src/rules/RulesEditor.test.tsx:145` | delete with undo › deletes without a confirm dialog, moves focus to the next rule and undoes via restore | MOVE | pending t9 |
| I216 | `src/rules/RulesEditor.test.tsx:166` | relationships on both ends › shows must-after as a ghost card above the flow with a variable chip | MOVE | pending t9 |
| I217 | `src/rules/RulesEditor.test.tsx:174` | relationships on both ends › shows the inverse badge on the other rule and in the list | MOVE | pending t9 |
| I218 | `src/rules/RulesEditor.test.tsx:184` | relationships on both ends › adds a relationship from the keyboard picker and badges it at once | MOVE | pending t9 |
| I219 | `src/rules/RulesEditor.test.tsx:195` | relationships on both ends › adds a relationship by dragging a rule from the list onto a slot | MOVE | pending t9 |
| I220 | `src/rules/RulesEditor.test.tsx:220` | relationships on both ends › removes a relationship with its x, from either end | MOVE | pending t9 |
| I221 | `src/rules/RulesEditor.test.tsx:230` | relationships on both ends › refuses a cycle and says why | MOVE | pending t9 |
| I222 | `src/rules/RulesEditor.test.tsx:240` | editing a typed trigger › preselects a probe rule's values and saves a changed command | MOVE | pending t9 |
| I223 | `src/rules/RulesEditor.test.tsx:268` | editing a typed trigger › keeps a label-only legacy trigger untouched when it is not edited | MOVE | pending t9 |
| I224 | `src/rules/RulesEditor.test.tsx:282` | editing a typed action › preselects the kind and saves a mapped param as its reference string | MOVE | pending t9 |
| I225 | `src/rules/RulesEditor.test.tsx:307` | editing a typed action › explains a missing required param in plain words and sends nothing | MOVE | pending t9 |
| I226 | `src/rules/RulesEditor.test.tsx:318` | editing a typed action › offers workflow outputs on a rule that has a workflow | MOVE | pending t9 |
| I227 | `src/rules/RulesEditor.test.tsx:327` | create, progressively › starts from 'New rule', asks 'When does this happen?' and creates a trigger-only rule | MOVE | pending t9 |
| I228 | `src/rules/RulesEditor.test.tsx:347` | create, progressively › never creates an event rule without a declared event, and says why in plain words | MOVE | pending t9 |
| I229 | `src/rules/RulesEditor.test.tsx:358` | create, progressively › creates a schedule rule with params.cron and tz | MOVE | pending t9 |
| I230 | `src/rules/RulesEditor.test.tsx:374` | create, progressively › an author check picks vars.trusted_authors and saves a var operand, not a copied list | MOVE | pending t9 |
| I231 | `src/rules/RulesEditor.test.tsx:404` | create, progressively › a typed list is still a literal list | MOVE | pending t9 |
| I232 | `src/rules/RulesEditor.test.tsx:424` | create, progressively › explains a refused save (%s) in plain words | MOVE | pending t9 |
| I233 | `src/rules/RulesEditor.test.tsx:446` | create, progressively › grows a rule through + : adds a condition, then a workflow | MOVE | pending t9 |
| I234 | `src/rules/RulesEditor.test.tsx:476` | pending human asks, in context › lists the rule's waiting ask and answers it with POST /asks/{id}/answer | MOVE | pending t9 |
| I235 | `src/rules/RulesEditor.test.tsx:490` | pending human asks, in context › offers a text answer when the ask has no options, and names an expired ask | MOVE | pending t9 |
| I236 | `src/rules/RulesEditor.test.tsx:506` | pending human asks, in context › shows no panel and no error when the rule has nothing waiting | MOVE | pending t9 |

### `web/src/rules/RulesLive.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I237 | `src/rules/RulesLive.test.tsx:42` | Rules tab live updates (h61 / c80) › subscribes to rules, runs, asks and rule decisions | MOVE | pending t9 |
| I238 | `src/rules/RulesLive.test.tsx:56` | Rules tab live updates (h61 / c80) › a rule toggled elsewhere shows here without a reload | MOVE | pending t9 |
| I239 | `src/rules/RulesLive.test.tsx:71` | Rules tab live updates (h61 / c80) › a new ask on a run of this rule appears when runs/asks change | MOVE | pending t9 |
| I240 | `src/rules/RulesLive.test.tsx:85` | Last runs shows the rule's contextual history (h78 / c97) › a superseded skip reads 'superseded by &lt;rule name&gt;' with an icon and a label | MOVE | pending t9 |
| I241 | `src/rules/RulesLive.test.tsx:110` | Last runs shows the rule's contextual history (h78 / c97) › a decision record arriving live is shown without a reload | MOVE | pending t9 |
| I242 | `src/rules/RulesLive.test.tsx:130` | Rules tab toggle in flight (#7) › a double click while the toggle is in flight sends one request and disables the switch | MOVE | pending t9 |

### `web/src/rules/StopRuns.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I243 | `src/rules/StopRuns.test.tsx:43` | stop current runs on disable › asks 'Stop N current runs?' without taking focus, and Approve stops them | MOVE | pending t9 |
| I244 | `src/rules/StopRuns.test.tsx:65` | stop current runs on disable › Keep running dismisses it and stops nothing | MOVE | pending t9 |
| I245 | `src/rules/StopRuns.test.tsx:78` | stop current runs on disable › is reachable and operable from the keyboard alone | MOVE | pending t9 |
| I246 | `src/rules/StopRuns.test.tsx:92` | stop current runs on disable › asks nothing when the disabled rule has no runs going | MOVE | pending t9 |
| I247 | `src/rules/StopRuns.test.tsx:100` | stop current runs on disable › a refused stop keeps the question open with the failure shown | MOVE | pending t9 |
| I248 | `src/rules/StopRuns.test.tsx:116` | stop current runs on disable › re-enabling the rule withdraws the question | MOVE | pending t9 |
| I249 | `src/rules/StopRuns.test.tsx:129` | rules API: active runs are response-only › a disable answer's active_runs are split off the rule | API: rules API helper, no route | pending t9 |
| I250 | `src/rules/StopRuns.test.tsx:140` | rules API: active runs are response-only › a save drops them too, so they are never sent back | API: rules API helper, no route | pending t9 |
| I251 | `src/rules/StopRuns.test.tsx:160` | a stop in flight never clobbers a newer offer › disabling another rule while a stop is pending keeps the new question | MOVE | pending t9 |
| I252 | `src/rules/StopRuns.test.tsx:184` | a stop in flight never clobbers a newer offer › a failed stop does not bring back an offer withdrawn by re-enabling | MOVE | pending t9 |

### `web/src/rules/TriggerPicker.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I253 | `src/rules/TriggerPicker.test.tsx:23` | event triggers › picks a surface, then a declared event, and writes params.type exactly | PICK | pending t9 |
| I254 | `src/rules/TriggerPicker.test.tsx:31` | event triggers › offers only the declared events of enabled apps, and no free-text field for the type | PICK | pending t9 |
| I255 | `src/rules/TriggerPicker.test.tsx:44` | event triggers › preselects an existing event and validates that one is picked | PICK | pending t9 |
| I256 | `src/rules/TriggerPicker.test.tsx:55` | run-finished triggers (d21) › offers the engine's run events as a built-in surface | PICK | pending t9 |
| I257 | `src/rules/TriggerPicker.test.tsx:73` | run-finished triggers (d21) › preselects the engine surface for a stored run-event trigger | PICK | pending t9 |
| I258 | `src/rules/TriggerPicker.test.tsx:79` | run-finished triggers (d21) › asks for a condition on the run's workflow, only for run events | PICK | pending t9 |
| I259 | `src/rules/TriggerPicker.test.tsx:89` | schedule triggers › writes a preset's cron | PICK | pending t9 |
| I260 | `src/rules/TriggerPicker.test.tsx:97` | schedule triggers › writes a custom cron and time zone | PICK | pending t9 |
| I261 | `src/rules/TriggerPicker.test.tsx:113` | probe triggers › writes actor, command, mode and schedule | PICK | pending t9 |
| I262 | `src/rules/TriggerPicker.test.tsx:129` | manual and existing triggers › manual takes no params | PICK | pending t9 |
| I263 | `src/rules/TriggerPicker.test.tsx:136` | manual and existing triggers › preselects the values of an existing typed rule | PICK | pending t9 |
| I264 | `src/rules/TriggerPicker.test.tsx:152` | manual and existing triggers › states a cron in words | PICK | pending t9 |

### `web/src/rules/relations.test.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I265 | `src/rules/relations.test.ts:18` | relationsOf › lists the relationships a rule declares and the ones pointing at it | PICK | pending t9 |
| I266 | `src/rules/relations.test.ts:32` | relationsOf › reads supersedes and may_after the same way | PICK | pending t9 |
| I267 | `src/rules/relations.test.ts:45` | withRelation / withoutRelation › adds a target once and removes it again, leaving the rest untouched | PICK | pending t9 |
| I268 | `src/rules/relations.test.ts:56` | canRelate › refuses a rule related to itself, an unknown rule and a duplicate | PICK | pending t9 |
| I269 | `src/rules/relations.test.ts:65` | canRelate › refuses a cycle in must_after and in supersedes | PICK | pending t9 |
| I270 | `src/rules/relations.test.ts:75` | badges on both ends › words each end of the relationship | PICK | pending t9 |
| I271 | `src/rules/relations.test.ts:87` | badges on both ends › badges the other end of a focused rule's relationships in the list | PICK | pending t9 |

### `web/src/workflows/IoEditor.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I272 | `src/workflows/IoEditor.test.tsx:32` | IoEditor: the in node › opens as the inputs editor with the workflow description | DETAIL | pending t9 |
| I273 | `src/workflows/IoEditor.test.tsx:41` | IoEditor: the in node › edits the workflow description | DETAIL | pending t9 |
| I274 | `src/workflows/IoEditor.test.tsx:47` | IoEditor: the in node › adds an input, then types its name, type, required and description | DETAIL | pending t9 |
| I275 | `src/workflows/IoEditor.test.tsx:69` | IoEditor: the in node › renaming an input follows the wires that read it | DETAIL | pending t9 |
| I276 | `src/workflows/IoEditor.test.tsx:81` | IoEditor: the in node › refuses a duplicate name with guided text, never raw text | DETAIL | pending t9 |
| I277 | `src/workflows/IoEditor.test.tsx:92` | IoEditor: the in node › removes an input | DETAIL | pending t9 |
| I278 | `src/workflows/IoEditor.test.tsx:101` | IoEditor: the out node › opens as the outputs and variables editor | DETAIL | pending t9 |
| I279 | `src/workflows/IoEditor.test.tsx:111` | IoEditor: the out node › adds an output and picks a step output port as its source | DETAIL | pending t9 |
| I280 | `src/workflows/IoEditor.test.tsx:122` | IoEditor: the out node › renames, retypes and removes outputs | DETAIL | pending t9 |
| I281 | `src/workflows/IoEditor.test.tsx:134` | IoEditor: the out node › keeps the textual source inspectable and editable as an advanced field | DETAIL | pending t9 |
| I282 | `src/workflows/IoEditor.test.tsx:148` | IoEditor: the out node › adds a variable, names and types it, and sets its default as JSON | DETAIL | pending t9 |
| I283 | `src/workflows/IoEditor.test.tsx:169` | IoEditor: the out node › Done closes | DETAIL | pending t9 |

### `web/src/workflows/RunForm.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I284 | `src/workflows/RunForm.test.tsx:50` | parseField › rejects a decimal for an integer and accepts it for a number | DETAIL | pending t9 |
| I285 | `src/workflows/RunForm.test.tsx:55` | parseField › parses object/array JSON and checks the shape | DETAIL | pending t9 |
| I286 | `src/workflows/RunForm.test.tsx:61` | parseField › any takes JSON or falls back to text | DETAIL | pending t9 |
| I287 | `src/workflows/RunForm.test.tsx:68` | RunForm › renders a control per declared input, marks required ones, fills model defaults, focuses the first | DETAIL | pending t9 |
| I288 | `src/workflows/RunForm.test.tsx:83` | RunForm › sends typed JSON values: number, boolean, parsed object | DETAIL | pending t9 |
| I289 | `src/workflows/RunForm.test.tsx:102` | RunForm › refuses a missing required input client-side, without calling the API | DETAIL | pending t9 |
| I290 | `src/workflows/RunForm.test.tsx:113` | RunForm › refuses a decimal integer and malformed JSON inline | DETAIL | pending t9 |
| I291 | `src/workflows/RunForm.test.tsx:127` | RunForm › shows a 422 invalid_inputs path next to its field as guided text | DETAIL | pending t9 |
| I292 | `src/workflows/RunForm.test.tsx:149` | RunForm › a non-422 failure is guided at the top and Escape closes | DETAIL | pending t9 |
| I293 | `src/workflows/RunForm.test.tsx:162` | RunOutputs › renders scalars readably and objects pretty-printed in a collapsible | DETAIL | pending t9 |
| I294 | `src/workflows/RunForm.test.tsx:171` | RunOutputs › says so when there are no outputs | DETAIL | pending t9 |

### `web/src/workflows/StepEditor.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I295 | `src/workflows/StepEditor.test.tsx:64` | StepEditor: every Step field › round-trips a fully populated step unchanged | DETAIL | pending t9 |
| I296 | `src/workflows/StepEditor.test.tsx:71` | StepEditor: every Step field › keeps every other field (unknown config keys included) when one field is edited | DETAIL | pending t9 |
| I297 | `src/workflows/StepEditor.test.tsx:78` | StepEditor: every Step field › edits description | DETAIL | pending t9 |
| I298 | `src/workflows/StepEditor.test.tsx:84` | StepEditor: every Step field › edits timeout and clears it to unset | DETAIL | pending t9 |
| I299 | `src/workflows/StepEditor.test.tsx:93` | StepEditor: every Step field › edits each retry field | DETAIL | pending t9 |
| I300 | `src/workflows/StepEditor.test.tsx:109` | StepEditor: every Step field › clearing every retry field unsets retry | DETAIL | pending t9 |
| I301 | `src/workflows/StepEditor.test.tsx:117` | StepEditor: every Step field › toggles a port's required flag | DETAIL | pending t9 |
| I302 | `src/workflows/StepEditor.test.tsx:127` | StepEditor: every Step field › offers a runner's commands and typed args, preserving unknown config keys | DETAIL | pending t9 |
| I303 | `src/workflows/StepEditor.test.tsx:143` | StepEditor: every Step field › edits config as key/value pairs for a non-runner step | DETAIL | pending t9 |
| I304 | `src/workflows/StepEditor.test.tsx:155` | StepEditor: every Step field › edits config as raw JSON behind the advanced toggle | DETAIL | pending t9 |
| I305 | `src/workflows/StepEditor.test.tsx:167` | StepEditor: every Step field › shows a guided error for broken JSON and blocks Done | DETAIL | pending t9 |
| I306 | `src/workflows/StepEditor.test.tsx:181` | StepEditor: guided validation › rejects timeout %s with a guided error and blocks save | DETAIL | pending t9 |
| I307 | `src/workflows/StepEditor.test.tsx:189` | StepEditor: guided validation › rejects %s = %s | DETAIL | pending t9 |
| I308 | `src/workflows/StepEditor.test.tsx:205` | StepEditor: guided validation › recovers when the value is fixed | DETAIL | pending t9 |

### `web/src/workflows/WorkflowList.test.tsx`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I309 | `src/workflows/WorkflowList.test.tsx:57` | WorkflowList (the Workflows tab's left pane) › renders one row per workflow under the New workflow button, in a Workflows nav | LIST | pending t9 |
| I310 | `src/workflows/WorkflowList.test.tsx:68` | WorkflowList (the Workflows tab's left pane) › links each row to /workflows?id=&lt;id&gt;; the selected row carries aria-current | LIST | pending t9 |
| I311 | `src/workflows/WorkflowList.test.tsx:80` | WorkflowList (the Workflows tab's left pane) › Enter on a focused row opens it (the rows are Tab stops) | LIST | pending t9 |
| I312 | `src/workflows/WorkflowList.test.tsx:96` | WorkflowList (the Workflows tab's left pane) › the row's (i) opens its description without opening the row (d19) | LIST | pending t9 |
| I313 | `src/workflows/WorkflowList.test.tsx:114` | WorkflowList (the Workflows tab's left pane) › the row switch shows enabled state and hands its workflow to onToggle | LIST | pending t9 |
| I314 | `src/workflows/WorkflowList.test.tsx:127` | WorkflowList (the Workflows tab's left pane) › New workflow calls onNew | LIST | pending t9 |
| I315 | `src/workflows/WorkflowList.test.tsx:134` | WorkflowList (the Workflows tab's left pane) › is there with no workflows and with one | LIST | pending t9 |
| I316 | `src/workflows/WorkflowList.test.tsx:140` | WorkflowList (the Workflows tab's left pane) › is there with exactly one workflow | LIST | pending t9 |
| I317 | `src/workflows/WorkflowList.test.tsx:146` | WorkflowList (the Workflows tab's left pane) › dots each row with its machine's palette slot, neutral when there is none | LIST | pending t9 |

### `web/src/workflows/io-read.test.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I318 | `src/workflows/io-read.test.ts:5` | readText › reads a chosen file's text through Blob#text | API | pending t9 |
| I319 | `src/workflows/io-read.test.ts:12` | readText › passes a read failure through as the rejection | API | pending t9 |

### `web/src/workflows/io.test.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I320 | `src/workflows/io.test.ts:35` | io names › refuses an empty, dotted/spaced or taken name, with a guidance code | API | pending t9 |
| I321 | `src/workflows/io.test.ts:45` | workflow inputs › adds a fresh, uniquely named input | API | pending t9 |
| I322 | `src/workflows/io.test.ts:52` | workflow inputs › renaming an input follows every wire and output source that reads it | API | pending t9 |
| I323 | `src/workflows/io.test.ts:61` | workflow inputs › removing an input drops its wires and unsets output sources that read it | API | pending t9 |
| I324 | `src/workflows/io.test.ts:68` | workflow inputs › a type change drops wires that no longer fit | API | pending t9 |
| I325 | `src/workflows/io.test.ts:79` | workflow outputs › adds, renames and removes outputs | API | pending t9 |
| I326 | `src/workflows/io.test.ts:88` | workflow outputs › a type change unsets a source that no longer fits | API | pending t9 |
| I327 | `src/workflows/io.test.ts:95` | workflow outputs › offers inputs, variables and step output ports whose type fits | API | pending t9 |
| I328 | `src/workflows/io.test.ts:107` | workflow outputs › checks a typed source reference: shape, target and type | API | pending t9 |
| I329 | `src/workflows/io.test.ts:117` | workflow variables › adds, retypes and sets defaults | API | pending t9 |
| I330 | `src/workflows/io.test.ts:125` | workflow variables › renaming a variable follows output sources; removing one unsets them | API | pending t9 |
| I331 | `src/workflows/io.test.ts:132` | workflow variables › a variable retyped out of an output's type unsets that output's source | API | pending t9 |

### `web/src/workflows/layout.test.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I332 | `src/workflows/layout.test.ts:28` | canvas bounds › measures each card from its port count | DETAIL | pending t9 |
| I333 | `src/workflows/layout.test.ts:34` | canvas bounds › a step with 8 ports fits inside the canvas | DETAIL | pending t9 |
| I334 | `src/workflows/layout.test.ts:43` | canvas bounds › a measured card taller than its estimate wins | DETAIL | pending t9 |
| I335 | `src/workflows/layout.test.ts:48` | canvas bounds › an empty graph still has the board's minimum height | DETAIL | pending t9 |

### `web/src/workflows/model.test.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I336 | `src/workflows/model.test.ts:30` | placement › the engine machine is the one with the engine_node role | API | pending t9 |
| I337 | `src/workflows/model.test.ts:36` | placement › labels each placement mode in words | API | pending t9 |
| I338 | `src/workflows/model.test.ts:43` | placement › resolves a step's machine through a machine, an actor's home, or not at all | API | pending t9 |
| I339 | `src/workflows/model.test.ts:59` | placement › setPlacement keeps exactly one of machine \| actor \| requirement | API | pending t9 |
| I340 | `src/workflows/model.test.ts:73` | typed ports › connects equal types, and anything to or from `any` | API | pending t9 |
| I341 | `src/workflows/model.test.ts:82` | typed ports › refuses a connection whose port types differ | API | pending t9 |
| I342 | `src/workflows/model.test.ts:93` | typed ports › wires a compatible connection, replacing whatever fed that input | API | pending t9 |
| I343 | `src/workflows/model.test.ts:108` | typed ports › wiring an output port to the outputs node sets that output's source | API | pending t9 |
| I344 | `src/workflows/model.test.ts:122` | typed ports › offers only type-compatible upstream ports for an input | API | pending t9 |
| I345 | `src/workflows/model.test.ts:131` | graph edges and machine hops › draws every wire plus the exported outputs | API | pending t9 |
| I346 | `src/workflows/model.test.ts:143` | graph edges and machine hops › marks cross-machine hops exactly as the board dashes them | API | pending t9 |
| I347 | `src/workflows/model.test.ts:157` | graph edges and machine hops › a requirement placement may land anywhere, so its hops are dashed | API | pending t9 |
| I348 | `src/workflows/model.test.ts:162` | graph edges and machine hops › with a run, the hosts that actually ran decide the dashes | API | pending t9 |
| I349 | `src/workflows/model.test.ts:171` | step edits › toggles a step's enabled flag | API | pending t9 |
| I350 | `src/workflows/model.test.ts:177` | step edits › deleting a step drops its wires and unhooks outputs it fed | API | pending t9 |
| I351 | `src/workflows/model.test.ts:184` | step edits › adds a uniquely named step | API | pending t9 |
| I352 | `src/workflows/model.test.ts:191` | step edits › renaming or retyping a port drops wires that no longer fit | API | pending t9 |
| I353 | `src/workflows/model.test.ts:206` | step edits › a PUT body carries only workflow.schema.json fields | API | pending t9 |
| I354 | `src/workflows/model.test.ts:214` | run overlay › maps each step to its host and outcome from persisted run state | API | pending t9 |
| I355 | `src/workflows/model.test.ts:221` | run overlay › lights the path the run took | API | pending t9 |
| I356 | `src/workflows/model.test.ts:241` | workflowMachine (the list row's dot) › is the one machine every step runs on | API | pending t9 |
| I357 | `src/workflows/model.test.ts:250` | workflowMachine (the list row's dot) › is neutral (null) when steps are mixed, unresolved, or there are none | API | pending t9 |
| I358 | `src/workflows/model.test.ts:264` | workflowMachine: actor placement resolves to a known machine (#7) › steps placed on thor-server show thor | API | pending t9 |
| I359 | `src/workflows/model.test.ts:268` | workflowMachine: actor placement resolves to a known machine (#7) › steps spanning two hosts stay neutral | API | pending t9 |
| I360 | `src/workflows/model.test.ts:274` | workflowMachine: actor placement resolves to a known machine (#7) › an actor's machine spelled differently (case, FQDN) maps to the enrolled machine's name | API | pending t9 |
| I361 | `src/workflows/model.test.ts:281` | workflowMachine: actor placement resolves to a known machine (#7) › an actor is found by name when the placement holds its name | API | pending t9 |
| I362 | `src/workflows/model.test.ts:286` | workflowMachine: actor placement resolves to a known machine (#7) › an actor on a machine nobody enrolled stays unresolved | API | pending t9 |

### `web/src/workflows/zoom.test.ts`

| ID | File:line | Describe › test | Fold impact | Folded test (t9) |
|----|-----------|-----------------|-------------|------------------|
| I363 | `src/workflows/zoom.test.ts:17` | canvas zoom (d19) › clamps to 25%–200% and rounds to two decimals | DETAIL | pending t9 |
| I364 | `src/workflows/zoom.test.ts:24` | canvas zoom (d19) › steps by 1.25 and stops at the bounds | DETAIL | pending t9 |
| I365 | `src/workflows/zoom.test.ts:31` | canvas zoom (d19) › fits a wide graph to the width, never above 1 (fit is also the reset) | DETAIL | pending t9 |
| I366 | `src/workflows/zoom.test.ts:43` | canvas zoom (d19) › reads out as a percentage | DETAIL | pending t9 |
| I367 | `src/workflows/zoom.test.ts:48` | canvas zoom (d19) › maps + = - _ 0 to in / out / fit, and leaves the browser's ctrl/cmd zoom alone | DETAIL | pending t9 |
| I368 | `src/workflows/zoom.test.ts:61` | canvas zoom (d19) › follows prefers-reduced-motion, and treats no matchMedia as reduced | DETAIL | pending t9 |
| I369 | `src/workflows/zoom.test.ts:70` | canvas zoom (d19) › scales the canvas height with the zoom, never under the board's | DETAIL | pending t9 |
