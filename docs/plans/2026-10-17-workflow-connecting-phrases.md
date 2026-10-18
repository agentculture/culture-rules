# Build Plan — workflow connecting phrases

slug: `workflow-connecting-phrases` · status: `exported` · from frame: `workflow-connecting-phrases`

> The Workflows tab reads as text, with no AI involved: each workflow carries a short paragraph and each step a one-line summary, both written by whoever builds the workflow. Simple shows that paragraph and then a paragraph built from the step lines, joined by fixed phrases derived from the step graph. With no workflow open, the tab hides the workflows pane and shows a card per workflow (name, step count, apps touched)

## Tasks

### t1 — Before-state evidence on main

- instruction: Read-only: cite main as of b146956. No code changes.
- covers: c19, h16, c20, h17
- acceptance:
  - A record (devague evidence or the plan's notes) quotes pr-fix's stored description starting 'd21, d30: quiet period', the SimpleView React Flow canvas in web/src/workflows/simple/SimpleView.tsx, and the list pane always rendered in web/src/routes/Workflows.tsx, each with file and line on main b146956

### t2 — Model: optional notes on Workflow and Step

- instruction: Add the field with doc() like description. Regenerate the JSON schema and openapi the way previous field additions did (grep the git log for openapi.json). Touch no describe, trust or web view code.
- covers: c11, h8
- acceptance:
  - `culture_rules`/model/workflow.py: Workflow.notes and Step.notes, free text, default empty; description keeps its name
  - Every committed docs/rules workflow loads unchanged; a round-trip through Workflow.`from_dict`/`to_dict` and through the API keeps description and notes
  - schemas/workflow.schema.json, api/openapi.json and web/src/api/types.ts carry notes; `test_openapi_contract` passes

### t3 — Boundary test: engine, node and actors never read description or notes; no drafting verb

- instruction: Extend the existing AST/grep style in tests/`test_boundaries.py`; allow `culture_rules`/model, store, server, io and cli.
- covers: c32, h29, c10, h7
- acceptance:
  - tests/`test_boundaries.py` (or a new test beside it) fails if any module under `culture_rules`/engine, `culture_rules`/node or `culture_rules`/actors reads a workflow's or step's description or notes
  - The same test asserts no 'summarize' CLI verb or MCP tool exists and no `summary_by` field is in the model
- obligation: `o9` (criterion 1) [engine, node and actors packages] Never read a workflow's or step's description or notes; a test enforces it (frame o10)

### t4 — Trust: versioned workflow digest (v1 and v2 pins)

- instruction: Keep `TRUSTED_WORKFLOWS` as the v1 set; add `TRUSTED_WORKFLOWS_V2` keyed by the same roles, with a comment naming the release and that v1 retires later as d20/d21 did. Print new digests from tests/rules/`test_trusted_workflow.py`'s helper.
- depends on: t2
- covers: c16, h13, h33
- acceptance:
  - `culture_rules`/actors/trusted.py: `workflow_digest_v2` hashes the definition without version, description and notes (workflow and every step, loop bodies included); `workflow_digest` (v1) is unchanged byte for byte
  - `workflow_role` trusts a run when its v1 digest is in the v1 pins or its v2 digest is in the v2 pins; an unknown digest under both is refused (fails closed)
  - v2 pins exist for the current committed pr-fixer workflows; tests: the pr-fixer-single fixture and every current committed definition keep their role; changing description or notes leaves v2 unchanged; any other field change changes it; tests/rules pass unchanged
- obligation: `o8` (criterion 2) [`culture_rules`/actors/trusted.py `workflow_role`] Fails closed: a digest not in the pins is never trusted; after the release, every pr-fixer chain run started on the current committed definitions keeps its role, and editing description or notes never changes a digest (frame o8)

### t5 — describe.py: conditions in words

- instruction: Add a sibling function; do not change `condition_text` or the d19 entries.
- covers: c29, h26
- acceptance:
  - `culture_rules`/model/describe.py gains `condition_words`(tree) covering every operator in `culture_rules`/model/condition.py (is, is not, is one of, is not set, and/or/not, comparisons); one unit test per operator
  - `condition_text` output (symbols) is byte-identical before and after for every committed rule and workflow (snapshot test over docs/rules)
  - A rendered condition longer than a fixed cap (constant in describe.py) yields 'if its condition holds'

### t6 — paragraph.py: step paragraph from the phrase table

- instruction: Readiness semantics come from engine/runs.py (`_deps_done` for top level, `_earlier_iteration_steps_ok` for loop bodies); read them, do not import the engine. Use describe.`condition_words` for when/until. Keep paragraph.py free of rule logic (t7 adds it).
- depends on: t5
- covers: c3, h2, c4, h3, c5, h4, c14, h11, c25, h22, c26, h23, c27, h24, c28, h25, c30, h27
- acceptance:
  - New `culture_rules`/model/paragraph.py, stdlib only: PHRASES (one dict) and `step_paragraph`(workflow) returning ordered segments {phrase, step, text} plus a rendered string
  - Unit tests, one per construct: sequence, same-predecessor siblings (no when), siblings with two or more when ('Depending on the conditions:'), join on the transitive reduction (diamond test), when, retry, `on_error` skip, wait seconds, wait guard, `for_each`, `retry_until`, loop body in declared order, condition shared by every remaining step said once
  - Unknown step kind or malformed config yields the d19 `step_text` line for that step and never raises; an empty description yields a visible gap segment naming the step
  - Every phrase ends with ':'; a test over all 9 committed workflows (with descriptions from a fixture under tests/fixtures) asserts every joining phrase is a PHRASES value and every description appears verbatim
- obligation: `o1` (criterion 1) [`culture_rules`/model/describe.py paragraph builder] A pure, stdlib-only function: the same workflow always yields the same paragraph; every joining phrase is a value of the one phrase table; no network, clock or randomness (frame o1)
- obligation: `o2` (criterion 3) [`culture_rules`/model/describe.py on unknown input] Never raises: an unknown step kind, malformed config or missing field yields the d19 numbered line for that step, and the rest of the paragraph still renders (frame o2)

### t7 — paragraph.py: rule sentences around the step paragraph

- instruction: Reuse describe.`trigger_text`, `condition_words`, `action_text`; the editor's fold (web/src/fold/model.ts) defines which rules start a workflow and which continue into it - mirror that in Python.
- depends on: t6
- covers: c37, h35
- acceptance:
  - `rule_sentence`(rule) builds one sentence per entry-point rule from trigger, condition, placement and relationships via describe.py vocabularies; `then_sentence`(rules) builds the Then side (continues into, ends here, action, on failure); no rule name or description text is used
  - pr-fix's paragraph (with its starting rules from docs/rules/pr-fixer/rules) opens with one sentence per starting rule and ends with the Then sentence; an unknown trigger or action kind falls back to its raw name
- obligation: `o13` (criterion 1) [`culture_rules`/model/describe.py rule sentence] Builds a rule's sentence only from its trigger, condition, placement, relationships and action through the existing d19 vocabularies; an unknown trigger or action kind falls back to its raw name, never free text (frame o14)

### t8 — API, CLI and MCP: describe returns description and paragraph

- instruction: Route in `culture_rules`/server/app.py beside `describe_rule` (line ~1259); CLI in `culture_rules`/cli/`_commands`/workflows.py; MCP in `culture_rules`/mcp/tools.py.
- depends on: t7, t2
- covers: c31, h28, c7, h5
- acceptance:
  - GET /workflows/{id}/describe returns description, paragraph (segments and text), entries and lines; api/openapi.json pinned; `test_openapi_contract` passes
  - `culture-rules workflows describe <id>` prints description, then paragraph, then the d19 numbered lines; --json carries all; the MCP tool returns the same; the CLI/MCP parity test passes
  - No file under `culture_rules`/engine or `culture_rules`/node changes
- obligation: `o3` (criterion 1) [GET /workflows/{id}/describe (api/openapi.json)] Returns description, paragraph, entries and lines for every stored workflow, including one with no steps or no descriptions; the schema served equals api/openapi.json (frame o3)

### t9 — API: expected version on workflow updates

- instruction: Implement in server/service.py `_save` for kind workflows only, using tx.`update_if` or an equivalent compare on version, like `set_enabled`'s Conflict path. Do not touch web/ (t15 does).
- depends on: t8
- covers: c34, h34
- acceptance:
  - PUT /workflows/{id} requires the expected version (If-Match or a body field, decided once and pinned in api/openapi.json); a missing or stale one gets 409 naming the current version and writes nothing, checked inside the store transaction
  - `culture_rules`/client and the CLI/MCP workflow write verbs send the version they read; API tests cover current, stale and missing; rollout import (io/exchange.py) is unaffected or passes its own version
- obligation: `o12` (criterion 1) [PUT /workflows/{id} (server/service.py `_save`)] Writes only when the expected version equals the stored version, in the same transaction; otherwise 409 naming the current version, and nothing is written (frame o13)

### t10 — Rollout import keeps a stored description the committed file lacks

- instruction: Merge the stored description into the committed form inside `_change_for` before comparing; tests in tests/io.
- covers: c15, h12
- acceptance:
  - `culture_rules`/io/exchange.py: importing a workflow whose committed description (workflow or step) is empty over a stored one that has it keeps the stored description and reports no change for that alone; a committed non-empty description wins; every other field imports as before
- obligation: `o7` (criterion 1) [`culture_rules`/io/exchange.py import (rollout import step)] Never clears a stored non-empty description when the committed file's description is empty; every other field imports exactly as before (frame o7)

### t11 — migrate-notes: endpoint, CLI verb, MCP tool, explain and learn

- instruction: Follow rules migrate-typeless end to end (server/app.py ~1016, cli rules.py:35, mcp tools).
- depends on: t9, t2
- covers: c17, h14, c35, h32
- acceptance:
  - POST /workflows/migrate-notes (dry run unless apply) moves description to notes byte for byte for workflow and steps, one audited write per workflow, skips a workflow whose notes is already set, and a second apply reports 0 changes
  - 'culture-rules workflows migrate-notes \[--apply\] \[--json\]' via cli/`_nounlib.py` migration(), its MCP tool (parity test passes), an entry in explain/catalog.py and a mention in learn; teken cli doctor . --strict passes
  - The migration is one pure function `migrate_notes`(definition) -> definition in `culture_rules`/model, reused by the endpoint and by t11
- obligation: `o5` (criterion 2) [CLI 'workflows migrate-notes' ↔ MCP tool] Same verb, same arguments, same output, kept identical by the CLI/MCP parity test; dry run by default, --apply to commit, --json everywhere, exit codes 0/1/2 per the CLI contract (frame o5)
- obligation: `o6` (criterion 1) [migration endpoint over the store] A dry run writes nothing; --apply moves description to notes byte for byte, in one audited write per workflow; a second --apply changes nothing; a workflow with notes already set is left alone (frame o6)

### t12 — Migrate the committed docs/rules workflow files

- instruction: Run the pure function over the files with a one-off script in the scratchpad; commit only the JSON changes.
- depends on: t11, t4
- covers: c17, h14
- acceptance:
  - Every docs/rules/\*/workflows/\*.json equals `migrate_notes`() of its previous content (test); descriptions are empty, notes hold the old text
  - v1 digests of the migrated files are NOT required; v2 pins from t4 still match (v2 ignores description and notes); tests/rules and the bundle tests pass

### t13 — Web: overview cards with the pane hidden

- instruction: New web/src/workflows/list/Overview.tsx; Workflows.tsx only switches between overview and open workflow. Actors come from the existing actors API client.
- depends on: t2
- covers: c12, h9, c8, h6, c24, h21
- acceptance:
  - /workflows with nothing open renders no list pane and one card per workflow the API returns (name, description or 'No description yet', step count including loop bodies, apps touched); empty and error states; vitest
  - Apps touched: app-kind actors with a surface in `APP_SURFACES`, from action steps' params.actor, ai/`actor_task` actors and the starting rules' Then actions; unknown ids left out; a loop workflow shows its full step count
  - Opening a card shows the workflow with its pane; the pane toggle is a button with aria-expanded and works by keyboard
- obligation: `o10` (criterion 1) [/workflows overview] Lists exactly the workflows the API returns, with an empty state for none and an error state for a failed load, never a blank page (frame o11)
- obligation: `o11` (criterion 2) [apps touched on a card] Shows an app only for an enabled or disabled actor of kind app with a known surface; an unknown or deleted actor id is left out, never guessed (frame o12)

### t14 — Web: Simple shows description and the API paragraph

- instruction: Use web/src/api/describe.ts; extend its types from openapi. Keep the description editable inline (one line per step, the paragraph field for the workflow).
- depends on: t8
- covers: c13, h10
- acceptance:
  - web/src/workflows/simple/SimpleView.tsx renders no React Flow canvas: the workflow description (or a gap), then the paragraph segments from GET /workflows/{id}/describe with phrases styled and gaps visible
  - The editor builds no phrases itself (o4): a test shows Simple text equals the API paragraph for a fixture workflow
- obligation: `o4` (criterion 2) [web/src/api/describe.ts ↔ Simple view] The editor shows the paragraph the API returns and never builds its own; Simple and 'workflows describe --json' are identical for the same workflow version (frame o4)

### t15 — Web: Detailed gains the editors Simple drops, and notes

- instruction: Files: web/src/workflows/views/\*, StepEditor.tsx, IoEditor.tsx. Not SimpleView.tsx or Workflows.tsx.
- depends on: t2
- covers: c13, h10
- acceptance:
  - A vitest lists every field Simple edited before (entry trigger, condition, placement, attempt counting, shared conditions, continues into, ends here, on failure, runs) and finds an editor for each in Detailed
  - Detailed shows notes for the workflow and each step, editable; StepEditor's description field is labelled as the one-line description

### t16 — Web: saves send the expected version; conflict message

- instruction: Touch web/src/api/workflows.ts and the save handler only.
- depends on: t9, t13
- covers: c34, h34
- acceptance:
  - web/src/api/workflows.ts sends the version read; a 409 shows 'This workflow changed since you opened it' with a reload action and keeps the draft; a two-tab vitest gets the conflict on the second save

### t17 — Rollout plan places migrate-notes between upgrade and resume

- instruction: `culture_rules`/cli/`_commands`/rollout.py plan lines (~line 505) and the ops doc; do not add a new rollout state step unless the existing record model needs it.
- depends on: t11
- covers: c33, h30
- acceptance:
  - 'culture-rules rollout plan' lines and docs/operations rollout runbook list migrate-notes (dry run then --apply) after upgrade on every host and before resume; a test checks the step order

### t18 — Behavioural Playwright test and parity check in CI's web job

- instruction: Extend the existing web job Playwright suite; reuse t6's description fixture.
- depends on: t13, t14, t15, t16, t8
- covers: c21, h18, c22, h19
- acceptance:
  - Playwright against the fixture API: cards with no pane; open pr-fix; Simple shows description and the joined paragraph; notes in Detailed; fails if any is missing
  - The fixture (under web test fixtures and tests/fixtures, not docs/rules) has a workflow with no description and a step with none; 100% of steps appear in order with 0 invented phrases; editor text equals the API describe paragraph
  - Assumption c9 checked: the 9 committed workflows render How it runs with no fallback line, or the test lists the fallbacks

### t19 — Docs, CHANGELOG, prompt files and version 0.21.0

- instruction: Use the version-bump skill. Docs describe what is on disk.
- depends on: t17, t18, t12
- covers: c1, h1, c18, h15
- acceptance:
  - CLAUDE.md, QWEN.md, AGENTS.override.md, AGENTS.colleague.md describe description/notes, the paragraph and migrate-notes in step; markdownlint-cli2 over the tree passes; harness-smoke passes
  - version-bump minor to 0.21.0 with a CHANGELOG entry; a closeout table maps every announcement element (description, paragraph, phrases, rule sentences, cards, apps, notes migration, versioned digest, expected version) to the task criterion that proves it

### t20 — Live: roll out 0.21.0 through the CLI with migrate-notes

- instruction: Deploy steps need the operator's shell (memory: deploys-need-operator-shell); check each step's result before the next (rollout-steps-stop-on-error).
- depends on: t19
- covers: c23, h20
- acceptance:
  - Operator-run rollout via culture-rules rollout: idle, pause, upgrade, seed, import, migrate-notes dry run then --apply, a second dry run reporting 0, enable, resume, verify
  - On rules.culture.dev: 0 workflows still carry pre-migration text in description, notes holds it byte for byte (sampled against the audit trail), and the next pr-fixer chain run reaches publish-fix or ends for a reason other than `workflow_not_trusted`

## Risks

- [unknown_nonblocking] API tasks t2, t8, t9 and t11 each re-pin api/openapi.json; they are chained by dependencies so they never share a wave
- [unknown_nonblocking] The phrase wording (PHRASES values) is proposed in the spec artifact, not confirmed text; t6's acceptance pins behaviour, and the operator reviews the wording at t6's merge (task t6)
- [unknown_nonblocking] Wave 4: t9 changes api/openapi.json while t14 may regenerate web/src/api/types.ts from it; if types are generated, t14 rebases on t9 before merge (task t14)
