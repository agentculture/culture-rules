# No flow-specific code

> culture-rules holds no flow-specific code: the PR fixer is removed, every flow (newspaper, recordings, repo docs, merge prompt) runs on generic steps, and app code is only the integration surface - business logic lives in workflow configuration

## Audience

- The operator, who configures every flow live and reviews each release; agents (Claude, codex, Qwen) who change culture-rules and must find no flow to special-case; future integration authors writing an app as an extension

## Before → After

- Before: Today about 23% of `culture_rules` (16,800 of 71,600 lines) is written for one flow: the disabled PR fixer (13,300 lines, with hooks in the agent runner, rule firing and the daemon), four newspaper.\* built-ins, transcript.render with its own title, time zone and direction rules, and repodocs.sync with its folder and spec-link rules; app code carries flow defaults (cache name 'recording', merge method squash, accepted file types) and flow names in docstrings
- After: After the three releases: no module in `culture_rules` exists for one flow; every live flow (daily newspaper, Discord posts, recordings to the reader, repo docs, green-PR merge prompt, saves and sweeps) runs on generic steps and app actions whose choices are step or actor configuration; app code is integration surface only; the fixer's code, tests, bundle and live workflows are gone

## Why it matters

- Flow code in the engine made each flow change a release (PR #137 was code for a formatting choice), hid business rules where the operator cannot edit them (an Asia/Jerusalem time zone, a fixed merge method), and kept 13,300 lines of a disabled feature coupled to the agent runner, rule firing and the daemon

## Requirements

- Removing the PR fixer includes its live configuration: the 9 stored workflows that use its steps (pr-fix, pr-fixer, publish-fix, queue-add, queue-add-capacity, queue-progress, queue-stop, report-secrets, review-commit) and the 24 rules that start them (all disabled) are deleted live, with the operator's go-ahead, before any node runs a build without those steps
  - honesty: Before any node runs the R1 build, the release CLI lists 0 workflows that use a removed step and 0 rules that start one (each deleted with a dry run first, restorable with rules/workflows restore)
- The fixer removal deletes its code (22 modules, about 13,300 of `culture_rules`' 71,600 lines, incl. apps/sonarcloud.py and apps/gitguardian.py unless a live flow needs them), its about 64 dedicated test files and fixtures (tests/rules/fixtures/pr-fixer-single), the docs/rules/pr-fixer bundle, deploy/pr-fixer/, docs/operations/pr-fixer.md, the web editor's fixer queue events (TriggerPicker `QUEUE_EVENTS`, @rules-queue) and fixer examples in describe.py; fixtures and snapshots that embed fixer workflows are rewritten
  - honesty: After R1, grep finds no fixer module, test, bundle, deploy script or harness-smoke link in the repo, and the full suite, lint, scan-secrets and teken doctor pass
- Removing the fixer keeps every shared generic piece live flows use: the workflow/actor digest machinery in actors/trusted.py (github.merge refuses an unpinned actor via `doc_refusal`; the github-app pin stays, the reviewer pins and `TRUSTED_WORKFLOWS` go), `github_pr`.`source_rule_refusal` (moved before `github_pr.py` loses its fixer ports; `github_merge.py`:107), lineage.upstream/LineageError/`step_state` (run.read, `run_read.py`:26), select, the check-settle path (node/`checks_settle.py`, server/hooks/github.py, daemon `_settle`: github.pr.`checks_settled` feeds green-pr-prompt), github.pr/github.`pr_head`, `concurrency_key` on rules and `concurrency_pool` on actors (16 live rules and 12 actors use them)
  - honesty: After R1, tests/rules/`test_green_merge_flow.py`, the run.read tests and the transcript flow test pass unchanged, and a live green-PR prompt still settles and merges on a click
- Fixer couplings inside generic modules are cut, not moved: actors/agent.py drops `LOCKED_INSTRUCTIONS`/`instruction_digest` from review.py, the merge hint (`merge_hint`.`with_merge_paragraph`) and status notes (`fixer_status`); node/firing.py drops the queue dispatch claim (queue.`claim_dispatch`, firing.py:312); daemon.py drops the conflict-watch, reaction-watch and fixer-status stages and the queues/handoffs/`fixer_cooldowns` collections; github.comment drops the status-board hooks (github.py:257-305); `deploy_data` drops BUNDLE = pr-fixer; backup drops `fixer_status_comments`; describe.py drops fixer phrases
  - honesty: After R1, agent.py, firing.py, daemon.py and github.py import no fixer module, and the agent, firing and daemon tests pass with their fixer cases removed
- The generic building blocks cover what the flow-specific code does today, beyond R2/R3 as planned: a file read under a root (transcript.render reads under roots), a time-of-day format beside date.local, host-suffix matching and an http(s)-scheme check in list.filter, shape rules for unknown keys, defaults, regex and line counts, document.render with Markdown output, dir/lang and a Markdown-to-XHTML field option, a ledger with several recorded side effects per lease (the newspaper records email and library push in one document, releases the lease when unsettled, settles with no recipients)
  - honesty: For every behaviour of newspaper.\*, transcript.render and repodocs.sync listed in scope entry s5, a test configures the generic steps to give the same output as the coded step on the same input
- Business rules found in app and step code move to step or actor configuration, with no default that encodes one flow: transcript.render's title format, Asia/Jerusalem time zone (`transcript_render.py`:118), right-to-left-unless-en rule, fallback word and texts, sections and CSS; llm.complete's cache name and label 'recording' (llm.py:171,174); repodocs' include folders (repodocs.py:197), spec-link and slug rules and culture.yaml project keys; library.push's filename rule, default format and author; attachment.fetch's accepted types (`attachment_fetch.py`:124); audio.transcribe's window plan; github.merge's merge method; decider's model and domain defaults; apps/github.py PR fact fields; the fixed Discord command reply text
  - honesty: Each rule listed moves to a step or actor field with no flow-specific default; a test shows two different values of each field giving two different results
- Generic modules name no flow: docstrings, examples and constants in library.py, email.py, llm.py, message.py, apps/github.py, apps/sonarcloud.py, apps/`public_text.py`, engine and node modules drop newspaper, transcript/recording, fixer and devague; a CI test fails when a flow name appears in `culture_rules`
  - honesty: The CI test scans `culture_rules` for the flow names and fails on one planted name; it passes on the release branch

## Honesty conditions

- The announcement holds when c4, c6, c10, c11 and c16 hold after R3: fixer gone, live flows intact, rules in config, the flow-name test green, every live flow on generic steps and app actions
- transcript-to-reader runs end to end after R1 with select still registered (a flow test plus one live recording)
- Every constant left in an app module is a security boundary or a platform limit, and the module's docstring names which; a reviewer can check each against that list
- The spec names who reads each part: the operator (live config, releases), agents (code rules), integration authors (the extension boundary)
- The counts are reproducible: wc -l of the listed modules and grep for the named constants at main f1dbc8d give the stated numbers
- Each live flow listed is checked after R3 by reading its workflow: every step is a generic step or an app action
- Each cited case is real: PR #137 closed for a formatting choice, `transcript_render.py`:118 TIMEZONE, `github_merge.py` squash-only, and the agent.py/firing.py/daemon.py fixer imports
- Each number is read from live runs and the CI test after R3, not estimated

## Success signals

- A CI test finds 0 flow names (fixer, pr-fix, newspaper, transcript, recording, devague) in `culture_rules`; 0 built-ins named after a flow are registered; the next 3 scheduled daily newspapers and every recording in the 3 days after R3 complete on generic steps with 0 failed runs; the green-PR merge prompt still merges on a click

## Scope / boundaries

- select stays a generic step: the live transcript-to-reader workflow uses it, so removing the fixer does not remove select (only its fixer-specific docs)
- App code keeps only the integration surface: the API call and its error mapping, security boundaries (allowlists, path roots, secret handling, one-time codes, same-repo and head-sha checks, sanitisation caps) and platform limits (Discord 2000/100 characters, 5 tags, API page sizes and maxima). Everything else a flow chooses is configuration

## Non-goals

- History is not rewritten: specs, plans, deliveries and .devague records that mention the fixer stay as they are; only living docs (operations, actors, the four harness files, README) drop it

## Scope exploration

- `s1` — `live store: workflows list + rules list (release CLI, read-only)`: 37 stored workflows; 9 use fixer steps (pr-fix, pr-fixer, publish-fix, queue-add, queue-add-capacity, queue-progress, queue-stop, report-secrets, review-commit), 24 of 49 rules start them, none enabled. Flow-specific steps also used live by enabled flows: daily-newspaper (newspaper.sections/filter/render/issue), transcript-to-reader (transcript.render, select), repodocs-sync (repodocs.sync)
  - seeds: `c2`, `c3`
- `s2` — `footprint outside the core (tests/, docs/, web/src, deploy/, scripts/, api/openapi.json, harness files)`: fixer modules total ~13,300 LOC (gate 2230, queue 1497, review 1372, `checks_settle` 1152, handoff 1020, `status_board` 907, `fixer_status` 791, claims 714, ...); other flow code ~3,500 LOC (newspaper\* 1404, `transcript_`\* 753, repodocs 1355). ~64 fixer test files (~30,800 lines). docs/rules/pr-fixer is 39 files; deploy/pr-fixer exists; scripts/harness-smoke.py:128 names docs/operations/pr-fixer.md. web: TriggerPicker.tsx:179-188,353 queue events; ActionPicker names transcript.render and repodocs.sync; fixtures embed fixer workflows. api/openapi.json has no fixer routes. CI workflows: none. CLAUDE.md 37 mentions, AGENTS.override 28, colleague 27, QWEN 23. History: specs 12, plans 22, deliveries 5, .devague 36 files
  - seeds: `c4`, `c5`
- `s3` — `fixer built-ins and support modules (actors/gate,review,handoff,trusted,lineage,merge_hint; node/actions/fixer_*,sonar,gitguardian,github_pr,select,queue; runner.py registrations)`: gate 2230, review 1372, handoff 1020 lines are fixer-only steps; shared and verified by grep: `github_merge.py`:258 trusted.`doc_refusal`, :107 `github_pr`.`source_rule_refusal`; `run_read.py`:26 lineage.upstream; agent.py:79 review.`LOCKED_INSTRUCTIONS`, :974 `merge_hint`; firing.py:312 queue.`claim_dispatch`; select used by live transcript-to-reader. Integration surfaces inside fixer code: GitHubThreadsPort (`github_pr.py`:976), GitHubReviewReplyPort (:900), GitHubPushPort (:470), apps/sonarcloud.py, apps/gitguardian.py
  - seeds: `c6`, `c7`
- `s4` — `engine/node/server hooks (chain_hold, claims, story_stop, checks_settle, conflict_watch, reaction_watch, status_board, fixer_status, firing, daemon, hooks/github, emit, rule, describe, deploy_data, backup, public_text, agent, limits)`: fixer-only whole files: `story_stop`, `conflict_watch` (vars `fixer_repos`), `reaction_watch`, `status_board` (907), `fixer_status` (791). Shared and kept: `checks_settle` (emits github.pr.`checks_settled` for green-pr-prompt; late path `late_event_id` serves pr-fixer-secrets-late), webhook receiver (fixer comment/stop triggers inside), claims (`reset_attempt_budget` for /fix), `chain_hold` (used by claims/runs/firing/daemon). No fixer CLI verbs, MCP tools or API routes; only trust (--force on trusted workflows) surfaces. Live: `concurrency_key` on 16 enabled rules, `concurrency_pool` on 12 actors
  - seeds: `c6`, `c7`
- `s5` — `newspaper.* builtins, transcript.render, repodocs.sync, email.send vs plan t11-t19 (docs/plans/2026-11-02-no-hardcoded-prompts.md:95-170)`: t11-t17 cover newspaper parse/filter/render/issue with gaps: site host-suffix match and http(s) scheme (`newspaper_render.py`:147-156, newspaper.py:249-250), masthead note across records (:379-387), Markdown-to-XHTML summary (:110-216), several side effects per lease and release-when-unsettled (`newspaper_issue.py`:57-77). transcript.render needs a file read under roots (:465-496), time-of-day format (:148-157), Markdown output (:296-316), dir/lang shell, and holds a content-addressed book cache (:401-450). repodocs.sync is mostly culture-library's wire contract; its git reads (ls-tree, cat-file, log trailers, first-parent merge, diff-tree, `subprocess_git` :230) are generic capabilities; PREFIXES (:197), spec location/slug (:255,:264), culture.yaml parsing (:312-370) are BL. email.send's `idempotency_key`, sender and `not_after` are generic; the 23 h window and key format live in `newspaper_issue` (:145,:201)
  - seeds: `c8`
- `s6` — `app code: culture_rules/apps/*.py and app action ports in node/actions (discord_*, message, github*, http, email, search, jira, library, llm, stt, decider, attachment_fetch, repodocs) plus transcript_render, transcript_file, checklist, markdown_render, markdown_html`: apps/ is mostly clean integration surface (protocol maxima, validators, timeouts). BL found: `transcript_render` whole format incl. TIMEZONE Asia/Jerusalem (:118) and RTL rule (:233-237); llm `DEFAULT_CACHE_NAME`/LABEL 'recording' (:171,:174); repodocs PREFIXES (:197), spec links, culture.yaml keys (:313-346); library filename-from-title (:233-243), default format/author; `attachment_fetch` `_TYPES` pdf/epub/md (:124); `audio_split` `MAX_WINDOW_S` 480; `github_merge` squash only; decider model clef/domain general (:96-97); apps/github.py `PR_FACT_FIELDS` (:80, docstring names the fixer); `discord_commands` fixed reply text (:106); github.py action status-board finalisation (:300). checklist, `markdown_render`, `markdown_html`, `discord_read`/react/reply, email, search, jira already near target. Flow names in docstrings of library.py, email.py, llm.py, apps/github.py, sonarcloud.py, `public_text.py` (spot-checked: `transcript_render.py`:118, llm.py:171,174, repodocs.py:197, `attachment_fetch.py`:124)
  - seeds: `c9`, `c10`, `c11`

## Decisions

- Operator 2026-11-06: 'App action is for integration and is ok. We add code that allows integration, with the thought of plugins for separation of core and extensions.' and 'Code for apps is for integrations, and only integration surface. BL is flow.'
- Releases in order (operator 2026-11-06, 'Recommendation sounds correct'): R1 removes the PR fixer from code and live; R2 adds the generic building blocks (R2/R3 of no-hardcoded-prompts widened); R3 switches the newspaper, recordings and repo-docs flows onto them and deletes newspaper.\*, transcript.render and the business rules in app code
- The core/extension boundary is drawn as its own release after the fixer removal: apps register their action kinds, built-ins, events and webhooks through one registry, core never imports an app module (a test checks it), and the package stays one distribution; separate plugin packages come later (operator 'confirm all', decider `boundary_only` 0.89)

## Open parks

- [unknown_nonblocking] Whether any live non-fixer rule relies on `chain_hold` (a concurrency key held across rules.run.\* continuations) and claims.`reset_attempt_budget`; the pocket chain and newspaper-to-discord continue from runs, so removing them needs a test against those rules first
- [unknown_nonblocking] transcript.render's content-addressed book cache (same transcript gives the same bytes, so the library dedupes): a generic cache step, part of the ledger, or dropped
