# Build Plan — No flow-specific code

slug: `no-flow-specific-code` · status: `exported` · from frame: `no-flow-specific-code`

> culture-rules holds no flow-specific code: the PR fixer is removed, every flow (newspaper, recordings, repo docs, merge prompt) runs on generic steps, and app code is only the integration surface - business logic lives in workflow configuration

## Tasks

### t1 — R1: move the shared helpers out of fixer modules

- instruction: Before anything is deleted: move `github_pr`.`source_rule_refusal` and its `_live` helper into node/actions/github.py; move lineage.upstream, LineageError and `step_state` into a generic module (engine/`run_chain.py`) and point `run_read.py` at it; in actors/trusted.py drop `ROLE_`\*, `TRUSTED_WORKFLOWS`/V2 and the reviewer pins but keep `actor_digest`, `doc_refusal`, `is_pinned`, workflow digests and the github-app pin. Record the baseline counts (wc -l of the fixer modules, `culture_rules` total, grep counts) in the PR description
- covers: c6, h4, c15, h11
- acceptance:
  - `github_merge.py` imports `source_rule_refusal` from node/actions/github.py and `run_read.py` imports upstream from engine/`run_chain.py`; tests/node/`test_github_merge`\*.py, tests/rules/`test_green_merge_flow.py` and the run.read tests pass unchanged
  - trusted.py holds no fixer role, workflow or reviewer pin; the github-app pin and `doc_refusal` remain and their tests pass

### t2 — R1: cut fixer hooks out of generic modules

- instruction: Edit only: actors/agent.py (drop `LOCKED_INSTRUCTIONS`/`instruction_digest`, merge hint, status notes, `fixer_status` imports), node/firing.py (drop queue.`claim_dispatch`/`dispatch_live`/`is_dispatch` and the fixer budget-reset prose; keep `chain_hold` - park v1), node/daemon.py (drop conflict-watch, reaction-watch and fixer-status stages and the queues/handoffs/`fixer_cooldowns`/reviews collections; keep `_settle`), node/actions/github.py (drop status-board hooks), server/hooks/github.py (drop /fix comment and stop triggers, keep check-suite settle arming), events/emit.py, `deploy_data`/`__init__.py`, ops/backup.py, model/describe.py, apps/github.py and apps/`public_text.py` docstrings. Remove the matching test cases only
- depends on: t1
- covers: c7, h5
- acceptance:
  - agent.py, firing.py, daemon.py, node/actions/github.py and server/hooks/github.py import no fixer module (an import test lists them); their remaining tests pass
  - check-suite webhooks still arm the settle and github.pr.`checks_settled` is still emitted (tests/node/`test_checks_settle.py` settle cases pass)

### t3 — R1: delete the fixer's steps, modules and tests

- instruction: Delete actors/gate.py, review.py, handoff.py, lineage.py (after t1 moved its generic part), `merge_hint.py`; node/actions/`fixer_cooldown.py`, `fixer_levels.py`, sonar.py, gitguardian.py, queue.py, `github_pr.py`; node/`story_stop.py`, `conflict_watch.py`, `reaction_watch.py`, `status_board.py`, `fixer_status.py`; their runner.py registrations; github.push and github.`review_reply` from model/`action_kinds.py`; and the ~64 fixer test files and tests/rules/fixtures/pr-fixer-single. Keep select.py, apps/sonarcloud.py and apps/gitguardian.py only if a remaining module imports them, else delete. Rewrite snapshots and fixtures that embed fixer workflows
- depends on: t2
- covers: c3, h2, c4
- acceptance:
  - no fixer module or test file remains (grep for the deleted module names in `culture_rules` and tests finds nothing); select is still registered
  - github.push and github.`review_reply` are not in `ACTION_KINDS` nor registered; the full suite passes

### t4 — R1: remove the fixer from the repo outside the code

- instruction: Delete docs/rules/pr-fixer, deploy/pr-fixer, docs/operations/pr-fixer.md; fix scripts/harness-smoke.py:128; drop the fixer from living docs (docs/operations/\*, docs/actors/github.md push and trust text, run-events.md, automation-contract.md, demo.md, README) and the four harness files (CLAUDE.md, AGENTS.override.md, AGENTS.colleague.md, QWEN.md; .pi/SYSTEM.md has none). Leave specs, plans, deliveries and .devague untouched (c5)
- depends on: t3
- covers: h3
- acceptance:
  - grep -ri 'pr-fixer\|pr fixer\|fixer' outside docs/specs, docs/plans, docs/deliveries and .devague finds nothing; markdownlint, harness-smoke and teken doctor pass

### t5 — R1: remove the fixer from the web editor

- instruction: web/src only: TriggerPicker.tsx queue events and @rules-queue surface, github.push/`review_reply` in ActionPicker.tsx and actors/app-config.ts, review/gate/queue workflows in fixtures (workflows/fixture.ts, simple/fixture.ts, actors-fixture.ts, rules-fixture.ts, statistics/fixture.ts) and the tests that use them; keep `queue_depth` (generic machine metric)
- depends on: t3
- acceptance:
  - npm run typecheck, vitest and Playwright pass; grep for fixer, rules.queue, github.push and `review_reply` in web/src finds nothing

### t6 — R1: version, PR and live rollout with the fixer's live config removed

- instruction: Main agent; each live write with the operator's go-ahead, dry run first. Order: disable then delete (never purge) the 24 fixer rules and 9 workflows, delete the 4 fixer agent actors and 9 fixer-only variables, drop github.push/github.`review_reply` from github-app params.actions; rollout pause, upgrade spark/orin/spark2/thor + API, verify, resume. Drop no collection. Then check: a green-PR prompt settles and merges on a click, transcript-to-reader runs, github-app digest still matches
- depends on: t4, t5
- covers: c2, h1, c20, h16, c21, h17, c22, h18, c25, h20
- acceptance:
  - release CLI lists 0 workflows using a removed step, 0 fixer rules, actors, or fixer-only variables; `checks_settle_min_s`, `checks_settle_timeout_s`, `ignored_check_apps` remain; rules/workflows deleted not purged
  - rollout verify ok on 4 nodes; one live green-PR prompt merges on a click; one recording reaches the reader; the fixer collections still exist

### t7 — Boundary: an extension registry in core

- instruction: New core module (`culture_rules`/extensions.py or similar): an extension registers action kinds (ParamSpec), built-in steps, event types, webhook routes and node listeners; runner.py, model/`action_kinds.py`, server/app.py and node/daemon.py read the registry instead of importing apps. Standard library only; lazy imports per optional extra stay
- depends on: t6
- acceptance:
  - a test registers a fake extension (one action kind, one builtin, one event, one webhook route) and the runner runs it, the server serves the route and `action_kinds` lists it, with no core edit
  - the existing suite passes with the built-in apps registered through the registry

### t8 — Boundary: move each app's integration code to its extension side

- instruction: Per app (github incl. node/`checks_settle.py` and server/hooks/github.py; jira incl. server/hooks/jira.py; discord incl. the gateway listener; email/resend; search/brave; library; llm; stt; decider; http), move ports and app clients under the extension side and register them. Pure moves plus registration; no behaviour change. Keep check settling working for the green-PR prompt
- depends on: t7
- covers: c26, h21
- acceptance:
  - an import test finds no module outside the extension side importing `culture_rules`.apps or an app port
  - the green-PR merge flow test, discord tests and the full suite pass unchanged

### t9 — Boundary: the API serves the action catalogue and the editor reads it

- instruction: Add a read-only route (and CLI/MCP parity) that returns the registered action kinds with their params; pin it in api/openapi.json; web/src/rules/ActionPicker.tsx builds its catalogue from it instead of the hard-coded `ACTION_KINDS` (ActionPicker.tsx:51-55)
- depends on: t7
- acceptance:
  - the OpenAPI contract test passes with the new route; a vitest case feeds a catalogue with an unknown extension kind and the picker offers it
  - typecheck, vitest and Playwright pass

### t10 — Boundary: version, PR and rollout

- instruction: Main agent; operator go-ahead for merge and each rollout step; paused rollout as for R1
- depends on: t8, t9
- acceptance:
  - rollout verify ok on 4 nodes; one green-PR prompt merges, one Discord save and one recording run succeed after the rollout

### t11 — R2: json.parse and shape rules

- instruction: Widened no-hardcoded-prompts t11: json.parse validates with model/shape.py; extend shape with unknown-key refusal, defaults, regex patterns and line counts. Generic names only
- depends on: t10
- acceptance:
  - configured with newspaper.sections' rules it refuses every case the coded check refused and accepts the rest (case table test)
  - shape regex, defaults, unknown-key and line-count rules each have a passing and a failing test

### t12 — R2: conditions contains, any, all and host suffix

- instruction: Widened t12: model/condition.py only; add contains (case-insensitive), any/all over a list, and a host-suffix match (host equals or ends with .suffix); no eval
- depends on: t10
- acceptance:
  - existing condition tests pass unchanged; each new op has passing and failing cases incl. subdomain matching

### t13 — R2: list.filter step

- instruction: Widened t13: keep items whose configured condition holds per item, dedupe by a field, derive a host from a url field refusing non-http(s), cap by a number or reference
- depends on: t12
- acceptance:
  - configured with newspaper.filter's rules it keeps exactly what the coded filter kept on the recorded cases

### t14 — R2: value helpers join, time.add, date.local with time of day

- instruction: Widened t14: join, time.add, date.local (IANA tz, DST-correct) with a format string for time of day (HHMM)
- depends on: t10
- acceptance:
  - date.local matches the coded newspaper and transcript results across a DST change; join and time.add give exact results

### t15 — R2: file.read under roots

- instruction: A generic step reading a file only under configured roots (symlinks resolved, size cap, text or JSON), reusing `transcript_file`'s path checks; outputs content or fields, never outside a root
- depends on: t10
- acceptance:
  - a path outside every root, a symlink escaping a root and an oversize file are refused before reading; a file under the root is read

### t16 — R2: document.render

- instruction: Widened t15: records to HTML, EPUB and Markdown from template, CSS and texts in config; per-record variant by condition and a cross-record note; dir/lang; a Markdown-to-XHTML field option; reuse `_epub.py`
- depends on: t10
- acceptance:
  - configured as newspaper.render and as transcript.render, it gives byte-identical output to the coded steps on golden inputs
  - identical input gives identical bytes; dir=rtl and lang appear on html, body and opf

### t17 — R2: lease and multi-effect ledger

- instruction: Widened t17: a key-addressed lease (owner, expiry, takeover, fence, `claim_lost`) and a ledger recording several side effects per key (frozen first payload, stable idempotency key, attempt before send, window then uncertain, settle, settle with no recipients, release when unsettled), collection and keys from config
- depends on: t10
- acceptance:
  - one test per guarantee of newspaper.issue plus fault injection at each fence: no double send, no skipped send, library push recorded once

### t18 — R2: registry, editor, docs, version, PR, rollout

- instruction: Register the R2 steps through the extension registry's core section, phrases, editor, docs (generic, no flow names), version bump; no live flow switch in R2
- depends on: t11, t13, t14, t15, t16, t17
- covers: c8, h6
- acceptance:
  - the R2 steps are registered and offered in the editor; full gate green; rolled out with verify ok

### t19 — R3: business rules in app code become config

- instruction: Per c10: llm cache name/label default 'recording' removed (`cache_name` required with cache), library filename rule/default format/author, attachment.fetch accepted types, audio.transcribe window plan, github.merge merge method, decider model/domain defaults, apps/github PR fact fields, discord command reply text become step or actor fields with no flow default; update live actors/steps that relied on a default in the same release; label every remaining app constant security or platform
- depends on: t18
- covers: c9, h7, c10, h8
- acceptance:
  - for each listed rule two config values give two results in a test; no flow-specific default remains
  - each remaining constant in apps and app ports carries a security or platform label

### t20 — R3: repodocs rules to config

- instruction: repodocs.sync stays an app action for culture-library's contract; PREFIXES, spec location and slug rule, Spec trailer and delivery rules, culture.yaml project keys move to actor or step config; the live repodocs-sync workflow gets them explicitly
- depends on: t18
- acceptance:
  - with today's values as config the sync pushes the same files and spec links as before (recorded-repo test); a different prefix list changes what is pushed

### t21 — R3: newspaper and recordings configured on generic steps

- instruction: Build workflow bodies for daily-newspaper and transcript-to-reader on R2 steps (committed bundles updated, no flow names in code); flow tests run end to end with the old built-ins still registered
- depends on: t18
- acceptance:
  - the committed newspaper and pocket-transcribe flow tests pass on generic steps only and match the coded output on recorded inputs

### t22 — R3: version, PR, rollout and live switch with old built-ins registered

- instruction: Main agent; operator go-ahead per write; save each live revision first; switch daily-newspaper, transcript-to-reader and repodocs-sync; keep newspaper.\* and transcript.render registered; check 3 newspapers, 3 days of recordings, 3 repodocs cycles
- depends on: t19, t20, t21
- covers: c23, h19, c16, h12
- acceptance:
  - each switched flow's saved revision applies by dry run; the 3-run checks are read from live runs with 0 failed runs

### t23 — R3b: delete the flow built-ins and enforce no flow names

- instruction: After t22's checks: delete newspaper.py, `newspaper_render.py`, `newspaper_issue.py`, `transcript_render.py` and their registrations/tests; strip flow names from docstrings; add the CI test that fails on fixer, pr-fix, newspaper, transcript, recording, devague in `culture_rules`; docs name who reads what (operator, agents, integration authors); delivery summary records why (c17)
- depends on: t22
- covers: c11, h9, c18, h14, c1, h15, c14, h10, c17, h13
- acceptance:
  - the flow-name CI test fails on a planted name and passes on the branch; no built-in named after a flow is registered
  - after rollout, 3 scheduled newspapers and 3 days of recordings complete with 0 failed runs and a green-PR prompt merges on a click

## Risks

- [follow_up] `chain_hold` and claims.`reset_attempt_budget` stay in R1 (frame park v1); whether any live non-fixer chain needs them is checked before a later removal (task t2)
- [unknown_nonblocking] transcript.render's content-addressed book cache (frame park v2): keep same-bytes-for-same-transcript as a document.render option or a ledger entry; decide while building t21 (task t21)
- [follow_up] Fixer leftovers on thor, orin and spark2 (bridges, `FIXER_`\* grant secrets) are an operator machine step after R1 (frame park v3) (task t6)
- [unknown_nonblocking] Fixer actors cannot be disabled while an enabled workflow uses them (`actor_in_use`): t6 deletes rules and workflows before actors (task t6)
- [unknown_nonblocking] Four rollouts (R1, boundary, R2, R3 + R3b); each follows docs/operations/rollout.md (pause, upgrade 4 nodes + API, verify, resume) and skips import, which replaces customised live workflows
