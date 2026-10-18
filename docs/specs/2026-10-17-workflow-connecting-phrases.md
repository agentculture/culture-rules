# workflow connecting phrases

> The Workflows tab reads as text, with no AI involved: each workflow carries a short paragraph and each step a one-line summary, both written by whoever builds the workflow. Simple shows that paragraph and then a paragraph built from the step lines, joined by fixed phrases derived from the step graph. With no workflow open, the tab hides the workflows pane and shows a card per workflow (name, step count, apps touched)

## Audience

- The operator and the agents who read and author culture-rules workflows: in the web editor at rules.culture.dev, and through the CLI/MCP 'workflows describe'

## Before → After

- Before: Today a workflow's description holds technical notes (pr-fix: 'd21, d30: quiet period -> GitGuardian hold ...'), Simple shows a flow chart of cards, the Workflows tab always shows the list pane beside one open workflow, and 'workflows describe' prints numbered config lines (d19) with no plain account of what the workflow is for
- After: Opening Workflows shows a card per workflow (name, description or a no-description placeholder, step count, apps touched) with no list pane. Opening one, Simple reads as text: the workflow's description, then one paragraph of its step descriptions joined by fixed phrases from the step graph ('Once that is done, ...', 'At the same time, ...', 'If the issue has results, ...'). 'workflows describe' prints the same paragraph; technical notes sit in notes, shown in Detailed

## Why it matters

- A reader cannot tell what a workflow does or why without reading its config or decoding d-numbers; the text that would tell them has nowhere to live, because description is taken by notes

## Requirements

- Connecting phrases come from the engine's real step semantics, not from edge kinds: Edge is port-to-port data wiring with no kind, and a step runs once every step feeding it through an edge is done. The phrase sources are a data dependency (sequence), steps with the same predecessors and no edge between them (at the same time), config.when (if), config.`on_error` skip (even if it fails), a retry policy (trying up to N times), a wait step's seconds (after waiting) and guard `head_unchanged` (unless the PR moves), `for_each` (for each item) and `retry_until` (until its condition holds)
  - honesty: A unit test per construct (sequence, same-predecessor siblings, when, `on_error` skip, retry, wait seconds, wait guard, `for_each`, `retry_until`) builds a minimal workflow and asserts the exact phrase; whether two steps get 'at the same time' is decided only by the edge graph (neither reaches the other through edges), matching engine/runs.py `_deps_done`
- One fixed, unit-tested phrase per construct, held in one table; every phrase in the paragraph traces to a graph fact or a config key of the step it joins, and a test shows 0 invented lines: the paragraph contains only stored summaries and phrases from the table
  - honesty: The phrase table is one dict in `culture_rules`/model/describe.py; a test walks every committed workflow's paragraph and asserts each joining phrase is a value of that table, and the rest of the text is a stored description
- A step whose construct has no phrase in the table, or an unrecognised config, falls back to today's numbered line, never a guessed phrase
  - honesty: A test feeds a step of an unknown kind and a step with an unrecognised config and gets the d19 numbered line for it, with no phrase; describing never raises
- Each overview card also lists the apps the workflow touches, derived from its steps' actors (an action step's config.action.params.actor, an ai or `actor_task` step's actor) resolved against the actors list to their app type (github, jira, discord, email, search)
  - honesty: Apps touched lists an app type only for actors whose type is app and whose surface is github, jira, discord, email or search; an unknown or deleted actor id is left out, never guessed; a workflow with none shows no apps line
- description means plain description: Workflow.description holds the workflow's short plain-language paragraph and Step.description its one-line summary (operator, 2026-10-17). Technical notes move to a new optional field named notes on Workflow and Step (Python model, api/openapi.json, web types); a workflow or step without either loads and round-trips unchanged
  - honesty: Every committed workflow loads with and without description and notes; a round-trip through the API returns both unchanged; Step.description and Workflow.description stay the same field names (no rename), notes is new
- With no workflow open, /workflows hides the workflows pane and shows one card per workflow the API returns (name, step count, apps touched); opening a card shows the workflow with its pane, which can be closed and reopened
  - honesty: A web test opens /workflows with no workflow selected: no list pane is rendered, one card per workflow the fixture API returns, an empty list shows an empty state; opening a card shows the pane, whose toggle is a button with aria-expanded and works by keyboard
- Simple stops showing the flow chart and shows only text: the workflow's paragraph, then the built step paragraph; every field Simple edits today (entry trigger, condition, placement, continues into, ends here, on failure, runs) keeps an editor in Detailed
  - honesty: Simple renders no React Flow canvas; a test lists every field Simple edits today (entry trigger, condition, placement, attempt counting, shared conditions, continues into, ends here, on failure, runs) and finds an editor for each in Detailed
- A step with no summary shows as a visible gap in the paragraph, never invented text
  - honesty: A step with an empty description renders a visible gap marker with its step name in Simple and in describe, never filler; the test fixture has at least one such step
- The authored description survives a rollout: importing a committed workflow file whose description is empty does not erase a description stored from the editor; committed files carry notes (technical, code-side), and a committed file that does carry a description still wins
  - honesty: A test imports a bundle whose workflow has an empty description over a stored workflow that has one: the stored description remains and import reports no change for it alone; a committed file that carries a description still wins; every other field imports as before
- The trust digest becomes versioned: v2 hashes the workflow without version, description and notes (workflow and step level), v1 stays today's function. `workflow_role` trusts a run when its v1 digest is in the v1 pins or its v2 digest is in the v2 pins, so chains crossing the rollout keep their role and editing prose never changes a v2 digest; the v1 set is retired in a later release, as the d20/d21 pins were
  - honesty: A test shows that changing description or notes (workflow or step) leaves `workflow_digest` unchanged while any other field change still changes it; `TRUSTED_WORKFLOWS` gains the new digests and keeps the old ones, and tests/rules/`test_trusted_workflow.py` passes
  - honesty: Tests: a run pinned to each historical definition in tests/rules/fixtures keeps its role through its v1 pin; the current committed definitions are trusted through v2 pins; changing description or notes leaves the v2 digest unchanged and any other field change still changes it; an unknown digest under both versions is refused
- A migration moves today's technical description text into notes and leaves description empty for an author to write: 'culture-rules workflows migrate-notes' (dry-run by default, --apply commits, --json) over a server endpoint, in the pattern of 'rules migrate-typeless' and 'runs backfill-ids', with its MCP tool; the committed docs/rules workflow files are moved the same way in the same change. A workflow whose notes is already set is left alone (re-running is a no-op)
  - honesty: Dry run lists each workflow and step it would move and writes nothing; --apply moves description to notes byte for byte and empties description; a second --apply reports 0 changes; the committed docs/rules files after the change equal the migration's output for them
- Step count on a card counts every step including loop bodies; apps touched also include the actors of the Then-side actions of the rules that start the workflow (they read as part of the workflow in the editor)
  - honesty: A card test with a loop workflow (daily-newspaper: 10 steps, 2 `for_each` with bodies) shows the full count, and a workflow whose rule's Then action posts a github.comment lists GitHub
- Siblings with no edge between them read 'at the same time' only when at most one of them has config.when; when two or more carry a when, they read 'Depending on the conditions:' and the join after them reads 'Then', never 'Once both are done' (review-commit's `review_codex` and `review_qwen` exclude each other through when, so only one runs)
  - honesty: review-commit's paragraph contains 'Depending on the conditions' and neither 'at the same time' nor 'once both'; pr-fix's threads and sonar (no when) still read 'at the same time'
- Inside a loop body the phrases follow declared order ('Then:'), never 'at the same time': body steps run one after another in declared order (engine/runs.py `_earlier_iteration_steps_ok`), whatever their edges say
  - honesty: A test with a `for_each` body of two unconnected steps renders 'Then:' between them
- Join phrases are chosen on the transitive reduction of the edge graph: an edge already implied by a longer path never adds a 'once X and Y are done' (daily-newspaper's render has five incoming edges, all but one implied by the chain)
  - honesty: daily-newspaper's render is introduced by 'Then:' alone; a unit test with a diamond (A to B, A to C, B and C to D, A to D) gives D 'Once both are done' naming only B and C's branch
- A config.when identical on every remaining step at the same level is said once ('From here on, only if `<condition>`:') rather than repeated (daily-newspaper repeats 'deliver = true' on 8 of 10 steps; pr-fix's fix body repeats each fixer condition on 2-3 steps)
  - honesty: daily-newspaper's paragraph contains 'deliver' at most twice; a step whose condition differs from the run in force still gets its own 'If'
- Conditions read in words: describe.py gains a words rendering of condition operators (= is, ≠ is not, ∈ is one of, missing is not set) beside today's symbol rendering, which d19's numbered lines keep; a rendered condition over a fixed length falls back to 'if its condition holds' with the full condition in Detailed
  - honesty: `condition_text`'s symbol output for d19 lines is byte-identical before and after; the words form of every operator in model/condition.py has a unit test; a condition past the cap renders 'if its condition holds'
- Joining grammar is fixed: each phrase ends in a colon and the step's description follows unchanged as its own sentence (no lower-casing, no trimming), so author text is never rewritten; descriptions are written as standalone sentences
  - honesty: For every committed workflow with fixture descriptions, each description appears verbatim in the paragraph; every phrase in the table ends with a colon
- The describe response keeps entries and lines (d19) and adds the workflow description and the paragraph as separate fields; the CLI prints the description, then the paragraph, then the numbered lines; --json carries all three; api/openapi.json pins the new fields
  - honesty: `test_openapi_contract` passes with the new describe fields; 'culture-rules workflows describe pr-fix' still prints the d19 numbered lines after the paragraph; --json has description, paragraph, entries and lines
- migrate-notes runs inside a rollout after the upgrade step has reached every host and before resume (steps: idle, pause, upgrade, seed, import, enable, resume, verify); the import step already writes the migrated committed files, so the verb covers stored workflows that are not in docs/rules
  - honesty: The rollout runbook and 'rollout plan' lines place migrate-notes between upgrade and resume; a dry run during the live rollout lists only workflows not in docs/rules (or none)
- Workflow updates carry the version they were read at: the API refuses an update with a missing or stale expected version (409) and writes nothing, so a stale editor tab, including one from the previous release that knows no notes, can never overwrite a newer workflow or drop migrated notes. The editor, CLI and MCP send the version they read
  - honesty: API tests: an update with the current version succeeds; a stale version gets 409 and the stored document is unchanged; a body with no expected version gets 409; the editor test saves twice from two tabs and the second gets the conflict message
- The new verb follows the CLI contract: workflows migrate-notes has an explain catalog entry, a mention in learn, an MCP tool kept identical by the parity test, and teken cli doctor --strict stays green
  - honesty: 'culture-rules explain workflows migrate-notes' prints an entry, learn mentions it, the MCP parity test lists it, teken cli doctor . --strict passes
- The constructed paragraph also reflects the rules: each rule that starts the workflow renders as one built sentence (its trigger, condition and placement, from describe.py's `trigger_text` and `condition_text`) before the step sentences, and the Then side (continues into, ends here, the action, on failure) as a built sentence after them; no rule free text is used (operator, 2026-10-17)
  - honesty: pr-fix's paragraph opens with one built sentence per entry-point rule (e.g. 'Starts when a pr-fixer-dispatch run asks for a fix, on spark2.') and ends with its Then sentence (continues into review-commit; on failure ...); every word comes from describe.py's vocabularies, and no rule's description or name text appears

## Honesty conditions

- Each part of the announcement (plain description, built paragraph, joining phrases, overview with the pane hidden, apps touched) maps to an acceptance criterion in the plan, and none ships as a placeholder
- No file under `culture_rules`/engine or `culture_rules`/node changes; api/openapi.json changes only by the notes field and the describe response, and `test_openapi_contract` passes
- grep finds no 'summarize' verb, no `summary_by` field and no proposed/confirm state for description; description is written through the same workflow write (API PUT, CLI/MCP workflows update) as any other field
- Every surface the spec changes is one the operator or an agent uses in the web editor or through culture-rules CLI/MCP
- Checkable on main before the build: pr-fix's description starts 'd21, d30: quiet period', Simple renders a flow canvas, and Workflows.tsx renders the list beside an open workflow
- The before-state quotes are reproducible from main (docs/rules/pr-fixer/workflows/pr-fix.json, 'culture-rules workflows describe pr-fix')
- A Playwright test in CI's web job against the fixture API sees the cards with no pane, opens one, reads the description and the joined paragraph in Simple, and finds notes in Detailed; it fails if any is missing
- The fixture with written descriptions lives under tests/ (not docs/rules, per commit-is-code-and-logic), and the parity test compares the editor's rendered paragraph with the API describe output for the same workflow
- Checked live on rules.culture.dev after the rollout: migrate-notes dry run, then --apply, then a second dry run reporting 0; a pr-fixer chain run after the rollout reaches publish-fix or ends for a reason other than `workflow_not_trusted`
- A test greps `culture_rules`/engine, `culture_rules`/node and `culture_rules`/actors for reads of a workflow's or step's description or notes and fails on any

## Success signals

- Against the 9 committed workflows (with descriptions written into a test fixture), the built paragraph covers 100% of steps in workflow order, every joining phrase comes from the phrase table and traces to a graph fact or config key (0 invented phrases), and the editor's Simple text and 'workflows describe --json' return identical paragraphs
- After 'workflows migrate-notes --apply' on the live store, 0 workflows or steps still carry text in description that was there before the migration, notes holds it byte for byte, and every pr-fixer chain run started after the rollout keeps its trusted role (no `workflow_not_trusted` refusals)

## Scope / boundaries

- The engine and run behaviour are unchanged: the paragraph is built in `culture_rules`/model/describe.py (stdlib only, no AI) and served through the existing GET /workflows/{id}/describe, so the CLI, MCP and editor read the same prose; api/openapi.json changes only for the new authored fields and the describe response
- No agent-drafting pipeline: no 'workflows summarize' verb, no `summary_by` author marker, no proposed/confirm state for prose. The paragraph and step lines are ordinary authored fields, written by a person in the editor or by an agent through the normal workflow write, like any other field (operator, 2026-10-17)
- Nothing outside display reads a workflow's or step's description or notes: no engine code, actor brief, action param or reference namespace (grep of `culture_rules` today: only model, store, describe and schema touch them). Excluding them from the trust digest is safe only while that holds; a test fails if engine, node or actors code reads them

## Non-goals

- No AI writes or rewrites the prose: step summaries and the workflow paragraph are written by the person or agent who builds the workflow, and the joining words come from a fixed phrase table (operator, 2026-10-17: 'No AI here - just the agent or human that builds them')
- Rules and actors keep their description as it is in this change: no notes field and no migration for them (operator, 2026-10-17)

## Assumptions

- The committed workflows in docs/rules serve as the phrase-table fixture: each of the 9 renders How it runs with no fallback line, or the fallbacks are listed in the test

## Scope exploration

- `s1` — `docs/specs/2026-10-12-workflows-overview-and-text-simple-view.md + .devague/plans/workflows-overview-and-text-simple-view.json (branch rules/workflow-text)`: The operator's restated ask (workflow paragraph, one line per step, text-only Simple, cards when no workflow is open) is already that spec's requirements; its plan t1-t12 is status proposed with nothing built. Issue 69's genuinely new part is the connecting phrases; its How it runs today joins step summaries with only numbers (h4)
  - seeds: `c2` (rejected)
- `s2` — `culture_rules/model/workflow.py Edge (line 76) + culture_rules/engine/runs.py _ready/_deps_done (line 2009)`: Edge has source, `source_port`, target and `target_port` only, with no kind; readiness is all edge predecessors done, so steps without a path between them really do run concurrently. Issue 69's suggested edge kinds (on failure, branch, parallel) do not exist as edges: failure-tolerance, branching and waiting are step config (`on_error`, when, wait seconds/guard, retry) and loop kinds
  - seeds: `c3`
- `s3` — `culture_rules/engine/runs.py module docstring (loops, wait steps, on failure d16, tolerated failure #53 t8)`: Rule-level `on_failure` is a Then-side action that the 2026-10-12 spec already renders as a built sentence; within the workflow, failure handling is config.`on_error` skip per step and RetryPolicy. `retry_until` carries config.until (a condition tree); wait steps are top-level only
  - seeds: `c3`
- `s4` — `docs/rules/*/workflows/*.json (9 committed workflows)`: The committed corpus exercises every construct: config.when (daily-newspaper render..record, review-commit reviewers), `on_error` skip (newspaper email), retry (newspaper email, push), wait seconds+guard (pr-fix quiet), wait seconds (queue-stop settle), `for_each` (newspaper search/summarise, publish-fix replies), `retry_until` (pr-fix fix), and same-predecessor siblings (review-commit `review_codex`/`review_qwen`). It is a ready test fixture for the phrase table; action steps name actors in config.action.params.actor, the source for apps touched
  - seeds: `c3`, `c5`, `c8`
- `s5` — `culture_rules/model/describe.py (501 lines, d19) + web/src/api/describe.ts + GET /workflows/{id}/describe`: A deterministic, no-AI describe already exists: fixed vocabularies per step kind, builtin, action kind and condition operator; `condition_text` renders a condition tree as words; it covers wait (seconds, guard), `for_each`, `retry_until`, config.when and retry attempts, but has no phrase for config.`on_error` skip. It deliberately ignores names, descriptions and other prose fields, and the CLI, MCP and the editor's (i) panel all read it. This is where the one phrase table goes
  - seeds: `c3`, `c4`, `c7`, `q2` (question, resolved), `q3` (question, resolved)
- `s6` — `culture_rules/io/exchange.py _change_for (line 309)`: SEEDS MIS-LINKED (ids counted off by one at capture time); superseded by the corrected entry recorded right after it. Import compares the committed file's stored form with the stored document and writes the committed form on any difference, so a committed workflow with no summaries would overwrite summaries written i
  - seeds: `c14`
- `s7` — `culture_rules/actors/trusted.py workflow_digest (line 108)`: SEEDS MIS-LINKED (ids counted off by one at capture time); superseded by the corrected entry recorded right after it. The digest hashes Workflow.`to_dict`(); new fields are inside it unless they are dropped explicitly, as version is
  - seeds: `c15`
- `s8` — `web/src/routes/Workflows.tsx`: SEEDS MIS-LINKED (ids counted off by one at capture time); superseded by the corrected entry recorded right after it. Today the tab always renders the workflow list beside the open workflow (aside, HeadTitle, RecentRuns); there is no overview state with the pane hidden
  - seeds: `c11`
- `s9` — `2026-10-12 spec on branch rules/workflow-text (read, not built)`: SEEDS MIS-LINKED (ids counted off by one at capture time); superseded by the corrected entry recorded right after it. That spec's non-AI parts (new short field rather than description, overview cards, closable pane, text Simple, Detailed keeps every editor, rollout survival, digest exclusion) are carried here as fres
  - seeds: `c9`, `c10`, `c11`, `c12`, `c13`, `c14`, `c15`
- `s10` — `culture_rules/io/exchange.py _change_for (line 309)`: Import writes the committed file's form whenever it differs from the stored document, so a committed workflow with no summaries would overwrite summaries written in the editor on every rollout (committed workflows carry none today)
  - seeds: `c15`
- `s11` — `culture_rules/actors/trusted.py workflow_digest (line 108)`: The digest hashes Workflow.`to_dict`(); new fields are inside it unless dropped explicitly, as version is
  - seeds: `c16`
- `s12` — `web/src/routes/Workflows.tsx`: Today the tab always renders the workflow list beside the open workflow; there is no overview state with the pane hidden
  - seeds: `c12`
- `s13` — `2026-10-12 spec on branch rules/workflow-text (read, not built)`: Its non-AI parts (a new short field rather than description, overview cards, closable pane, text Simple, Detailed keeps every editor, rollout survival, digest exclusion) are carried here as fresh proposed claims; its agent-drafting parts (summarize verb, `summary_by`, sso-only confirm) are dropped
  - seeds: `c10`, `c11`, `c12`, `c13`, `c14`, `c15`, `c16`
- `s14` — `docs/rules/*/workflows/*.json description fields (9 workflows, 34 steps)`: Every workflow description is technical (pr-fix starts 'd21, d30: quiet period ->', daily-newspaper '#53: one dated issue a day...'), and so are nearly all of the 30 step descriptions (builtin names, d-refs, issue numbers); only quiet and settle read plainly ('Wait 5 minutes; ...'). Moving all of them into notes leaves description empty everywhere, so the migration does not invent plain text
  - seeds: `c11`, `c17`
- `s15` — `culture_rules/actors/trusted.py workflow_digest + TRUSTED_WORKFLOWS (lines 15-21, 60, 108)`: The digest is sha256 over Workflow.`to_dict`() minus version, so description is inside it today; moving text to notes or dropping fields changes the digest of every trusted pr-fixer workflow. Pins are a set per role, and a legitimate change adds the new digest (tests/rules/`test_trusted_workflow.py` prints it), so old and new can coexist while pinned runs drain
  - seeds: `c16`
- `s16` — `culture_rules/cli/_commands/rules.py:35, runs.py:47 (migration helper)`: Existing migrations are a CLI verb calling a server endpoint through a shared migration() helper, dry-run by default; the description-to-notes migration follows the same pattern, and its MCP twin follows from the CLI-MCP parity test
  - seeds: `c17`
- `s17` — `web/src/workflows/StepEditor.tsx`: The step editor edits step description today; it becomes the one-line editor, with notes a separate field in Detailed
  - seeds: `c11`, `c13`
- `s18` — `challenge pass / adjacent-systems lens: review-commit.json, daily-newspaper.json, engine/runs.py _earlier_iteration_steps_ok`: Rendering the proposed phrase rules literally against three committed workflows (artifact VFRrVjBoPxaZGgDQdET3Hg) showed exclusive when-siblings read as parallel, loop bodies ignore edges, joins need transitive reduction, and identical conditions repeat
  - seeds: `c25`, `c26`, `c27`, `c28`
- `s19` — `challenge pass / unstated-assumptions lens: culture_rules/model/describe.py condition_text`: The operator's 'render the condition in words' (q2) assumed `condition_text` already reads as words; it renders symbols (=, ≠, ∈), and the d19 lines the operator liked depend on that symbol form
  - seeds: `c29`
- `s20` — `challenge pass / security lens: grep of culture_rules for description reads + actors/trusted.py`: Only model, store (version history), describe and schema touch workflow/step description; nothing feeds it to an agent. The digest change breaks every historical pin under `workflow_role`'s recompute, raised as blocking q5 on c16
  - seeds: `c32`, `q5` (question, resolved)
- `s21` — `challenge pass / migration, data-loss lenses: cli/_commands/rollout.py steps + server/service.py _save`: The rollout runs idle, pause, upgrade, seed, import, enable, resume, verify; pause stops dispatch but not API writes. Workflow saves are a full replace with no expected version, so a stale old-release editor tab drops migrated notes
  - seeds: `c33`, `c34`
- `s22` — `challenge pass / overlooked-actors lens: model/rule.py, actor JSON, web/src/fold/writes.ts`: Rules and actors carry technical description too; raised as question q2 (devague question), not decidable by the pass
- `s23` — `challenge pass / operations lens: CLAUDE.md CLI contract + teken gate`: A new verb needs an explain entry, a learn mention and an MCP twin, or the CI teken gate fails
  - seeds: `c35`
- `s24` — `challenge pass / observability, recovery lenses: audit.write in server/service.py _save`: Clean for recovery: every definition write records before and after in the audit trail, so a lost description or notes can be recovered from audit; nothing alerts on it, which is residual risk
- `s25` — `challenge pass / unexamined surfaces`: Not examined: sibling repos (culture, workledger-cli, agenda) that might read workflow description through the API; the backup/restore extra (only that io/exchange carries every field); the web bundle's localStorage view state across the Simple change; Statistics tab use of workflow text

## Hard questions

- How does 'If ...' read for config.when: the condition tree rendered into words by fixed templates per operator, or a fixed 'If its condition holds,' that leans on the step's own summary? (resolved: Operator, 2026-10-17: render the condition into words (the existing `condition_text` in `culture_rules`/model/describe.py does this today))
- Does the CLI/MCP 'workflows describe' print the same connected prose (CLI-MCP parity, the d19 describe format), or is it web-only? (resolved: Operator, 2026-10-17: both, one table: the CLI/MCP 'workflows describe' and the editor read the same connected prose from one Python phrase table)
- Apps touched: only app actors (github, jira, discord, email, search), or every actor a workflow reaches (agents, machines, humans)? (resolved: Operator, 2026-10-17: app actors only (github, jira, discord, email, search))
- The digest function change breaks the 16 historical pins in `TRUSTED_WORKFLOWS` (most kept 'while runs pinned to it may still push'); publish-fix checks the role of every run in its chain, including finished runs pinned to older definitions. Option A: recompute pins under the new function only for the current committed definitions; older ones fail closed. Option B: a versioned digest, where `workflow_role` accepts a run when its v1 digest is in the v1 pins or its v2 digest (without description and notes) is in the v2 pins, and the v1 set is retired later as d20 and d21 were (resolved: Operator, 2026-10-17: option B, a versioned digest)

## Open parks

- [unknown_nonblocking] Rollback to a release before notes: an older server reads stored workflows with strict=False and drops unknown fields, so a save from the old editor would drop the migrated notes
- [unknown_nonblocking] Sibling repos and the backup extra were not examined for reads of workflow description; a consumer that shows description would show plain text (or nothing) after the migration

## Resolved vagueness

- [unknown_nonblocking] Two people saving the same workflow at once: whether workflow writes check an expected version was not examined; prose edits add more writes to the same documents — resolved: Operator, 2026-10-17 (q1): workflow updates carry an expected version; c34
