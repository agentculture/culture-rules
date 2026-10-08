# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). This project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.16.0] - 2026-10-09

Deviation d26: the PR fixer says that it works, on the PR, in one status comment per fix chain, edited live. Ships over the live 0.15.0 (d21, d25); no pinned workflow changes.

### Added

- The PR fixer's live status comment (d26, `culture_rules/node/fixer_status.py` reads and renders, `culture_rules/node/status_board.py` writes): once the first run of a fix chain is past its quiet period and GitGuardian hold, the node on the App actor's machine posts one comment as the App (what started the chain, with the author and head SHA; the stage list; the chain's run link) and edits it as the stages move: agent working (try N of 3) with its latest status notes, the gate verdict, the review verdict and findings count, the pushed head or the failure, and the agent's fix summary. The comment's record (`fixer_status_comments`, a node collection backed up with the run history) is keyed by the chain's root run, so a re-fix reuses the comment.
- A single writer reconciling desired state (d26): one process writes an App actor's status comments, the holder of the actor's writer lease (`fixer_status_writers`, `{owner: host:pid:boot-random, until}`, 120 s, compare-and-set at every cycle and before every call; nodes keep their clocks within 30 s, `MAX_CLOCK_SKEW_S`: a holder starts no call past `until - 50 s`, another process takes the lease only after `until + 30 s` by its clock), and only a node on the actor's current placed machine (spark for the shipped `github-app`) tries; records are selected by actor, so moving the actor hands the writer over once the old lease expires; an App actor without a machine gets no live status (`status: true` posts a plain chain-end comment). Each cycle the writer renders the body the store's inputs describe (`desired_rev`) and edits the comment to it (or posts it) while GitHub has not acknowledged it; `acked_rev` moves only on a 2xx, ambiguous failures are retried with backoff, so the newest desired body always wins and a record is done only when the acknowledged body is the final one. Store writes are compare-and-set on the record's revision. A POST whose answer was lost stays `posting` and is resolved by listing the PR's comments (`GitHubApp.list_issue_comments`) for this App's comment with the chain's marker, else the record gives up silently. Writes keep a 5 s floor (notes alone a minute); failures back off to 15 minutes, 403/422 and refused repos give up after 3 tries; a final undelivered after 24 h or a record idle for 7 days gives up; a cycle makes at most 10 HTTP requests (each charged before it is sent through the new `GitHubApp.request_guard`) within 10 s after the drive stage, each call bounded by 20 s; records are selected per actor, due first, paged with a composite (`retry_at`, id) cursor; indexes are declared and final records are dropped after 30 days.
- The status stage's GitHub requests run under a watchdog (d26, opt-in through `GitHubApp.watchdog()`): a worker thread from a small bounded pool, waited on with the call's hard deadline, so dripping headers, chunks or trailers can no longer outlast it (`deadline_exceeded`). Every other caller (push, settle, threads, plain comments, the API) runs its requests inline, as before. The transport also reads the body in chunks against the deadline, with a size cap.
- `github.comment` takes an optional `status: true` (d26): the body becomes the chain's pending final, delivered by the node's status stage as the comment's final section with the engine's run link and marked final only once GitHub acknowledged it. The action resolves no credential, never calls GitHub and never fails the run. Outside an opted-in chain on the same PR it posts a plain comment (the text made inert, then the run link). It does not combine with `once_key` (`action_param_conflict` at save, `bad_input` at run time). `rules describe` reads `(as its chain's status comment)`. Every chain-end action and hand-back of the shipped fixer rules sets it; the GitGuardian findings comment (d25) stays separate.
- The agent's free status notes (d26): a bridge `progress` callback whose note carries `STATUS: <text>` (a shell tool call `echo "STATUS: ..."`, reported by its title) is normalized, checked and kept on the bridge invocation (the last 5); a refused note is counted and drops the kept notes. An agent step whose run writes a status comment gets a fixed hint appended to its instruction (never a locked brief). As deployed (cultureagent 0.14.0 with Qwen Code's preparing tool calls), the full title arrives in a `tool_call_update`, which the bridge does not describe: notes need a cultureagent change (docs/operations/pr-fixer.md, "The agent's status notes").
- `culture_rules/apps/public_text.py` (d26): untrusted text relayed into a public comment is normalized (NFKC, control and format characters and every URL removed), checked (credential formats and random strings over several views, so markup cannot split a token; known secret values of the process, any 12-character piece of them or of their hex/base64 encodings, so spacing, markup and note boundaries cannot split one), on raw, entity-decoded and normalized views and the stripped view of each, then escaped into inert Markdown (metacharacters escaped, `<` `>` `&` as entities, mentions neutralised). The rendered body is checked again as a whole before every post or edit; a refused section reads `[withheld]`; engine-validated facts (logins, SHAs, run ids, verdicts) get only the known-secret check, and a final body whose facts fail it falls back to a final built from safe facts (`**PR fixer finished**: <outcome>.` and the run link), never the working headline. The residual risk (an agent's own encoding) is documented.
- `culture_rules.actors.secrets.known_values()` (d26): the secret values the process holds (resolved through grant, injected `CULTURE_RULES_SECRET_*`, bridge bearer tokens), in memory only.
- `GitHubApp.update_issue_comment` and `GitHubApp.list_issue_comments` (d26).

### Changed

- The shipped fixer rules' chain-end texts no longer carry the agent's summary (the status comment shows it as the fix summary) or the run link (the engine links the run that ended the chain). `pr-fixer-publish`'s success line reads the gate verdict from its trigger: `publish-fix` exports no verdict, so it read `gate ,`.

## [0.15.0] - 2026-10-08

Deviation d25: a failed GitGuardian check is reported as a PR comment and never auto-fixed. Ships with the 0.14.0 split; roll out both together.

### Added

- GitGuardian findings are reported, never auto-fixed (d25): a new rule `pr-fixer-secrets` fires once per PR head SHA whose checks settle with GitGuardian's suite failed and runs the new workflow `report-secrets`, whose built-in `gitguardian.findings` (`culture_rules/node/actions/gitguardian.py`, parser `culture_rules/apps/gitguardian.py`) reads the GitGuardian check run as the App (new read-only `GitHubApp.list_check_runs`) and posts one comment: per finding the secret type, file:line, short commit, incident link and status (at most 50, then "… and N more"), and the remediation (revoke and rotate; rewriting history does not un-leak it). No secret value is quoted: only a recognizable findings table is read (the known header immediately followed by a separator of the same cell count, contiguous rows, never inside fenced or indented code, with CommonMark fence closing and tabs expanded), its columns cleaned of markdown, and no URL from the check is echoed (the incident and check links are rebuilt from validated digits and names); a `neutral` (too large to scan) check is no finding. A GitGuardian failure that completes after the settle is reported by `pr-fixer-secrets-late` on the new `github.pr.checks_failed_late` event (once per repo, head SHA and app). The comment is durable once per head SHA through the action's `once_key`, shared by both rules; the rules keep their own key (`pr-secrets:{repository}#{number}@{head_sha}`), outside the fixer's budget.
- The fixer never fixes a head GitGuardian fails on (d25): `pr-fixer-checks` gains `not (gitguardian ∈ failed_apps)`, and `pr-fix` gains a `secrets` step (built-in `gitguardian.hold`) after the quiet period that fails the run `secrets_found` while GitGuardian fails, so a `/fix` comment, a review or a re-fix never reaches the agent. Only the held `pr-fix` digest is trusted (d21's never ran live). Rollout: docs/operations/pr-fixer.md, "Rolling out the GitGuardian report (d25)".
- `github.pr.checks_settled` carries `failed_apps` (d25): the app slugs of the counted suites that concluded `failure`. A counted app's failure that completes after the settle emits `github.pr.checks_failed_late` (d25).
- `github.comment` takes an optional `once_key` (d25): the port claims (repo, PR, key) in the store (`github_comment_once`, backed up with the run history) before posting, so the comment is posted at most once across runs, rules, budget resets, restores and nodes; a claimed key completes `skipped: posted_before` (`claimed_before` when that post's outcome is unknown). Only a client error from GitHub (4xx but 408) releases the claim; a 5xx, 408, network error or deadline keeps it.
- The checks settler's event types (`github.pr.checks_settled`, `github.pr.checks_failed_late`) and id prefixes (`settled_`, `late_`) are reserved at external ingest (d25): the bus and the webhook sink quarantine them. Before, only the settler's internal source was reserved, so a forged settled event with another source was accepted. The schedule and probe namespaces (kind or type `schedule`/`probe`, sources `culture-rules/schedule` and `culture-rules/probe`, ids `schedule/*` and `probe/*`) are reserved the same way (pre-existing gap), and an envelope whose `type` is not a string is quarantined instead of raising and stalling ingest. A late failure is a candidate (`checks_settle_late`) emitted only once the App's current listing confirms the app's suite still failed, so no clock decides it; the recovery scan also notes candidates for stored late completions the webhook never handled, including those received up to 5 minutes before the settle (clock skew). Candidates carry a random token, drawn on every insert and every refresh, and a confirmer drops a candidate only at the token it read, so a stale clean listing never drops a newer failure (not even a candidate dropped and noted again: no ABA). Emitting the late event and dropping the candidate happen in one transaction that requires that token, so a stale failing listing never emits after a newer clean one dropped it. Every field `reserved_reason` reads (`id`, `type`, `kind`, `source`) must be a non-empty string when present, or the envelope is quarantined; nothing malformed can raise and stall ingest. Text that is not UTF-8 encodable (a lone surrogate, in any field or in `data`) is refused on the bus and in the webhook sink before it reaches the store, and quarantine records keep such text escaped (backslashreplace), so recording the refusal can no longer raise either. More generally (mostly pre-existing), ingest and the webhook sink never raise on envelope content: `reserved_reason` also refuses, iteratively, nesting deeper than 32, a key that is not a string, has NUL or starts with `$`, and an int outside signed 64-bit; such a refusal keeps only a bounded ASCII summary; and a known content error from the store (`is_content_error`: a driver `InvalidDocument`/`InvalidStringData`/`DocumentTooLarge`, `OverflowError`, `RecursionError`, `UnicodeError`) becomes a minimal, always-storable quarantine record (deduplicated on the full id) while the batch goes on. Anything else (an outage, `OperationFailure`, `WriteConcernError`, an unknown error) propagates: the bus batch is retried, so a valid event is never dropped; a failed webhook delivery stays missing until it is redelivered by hand (GitHub does not redeliver on its own).

### Changed

- Describe vocabulary: `gitguardian.findings` and `gitguardian.hold` read as the head's GitGuardian findings and the hold; a rule with one attempt reads `≤1 attempt`; a `github.comment` with a `once_key` reads `(once per once_key)`.

## [0.14.0] - 2026-10-08

0.13.0 is the delivery of plan pr-fixer-rule (#17); this
release follows it with deviation d21: the PR fixer split into chained rules and workflows.

### Added

- The PR fixer as three workflows chained by run events (d21 phase 2, docs/rules/pr-fixer): `pr-fixer-{checks,comment,review,review-comment}` run `pr-fix` (quiet period, trusted threads, the failing SonarCloud conditions, agent and gate: builds and gates one commit, never reviews or pushes it); `pr-fixer-review-commit` fires on `pr-fix` succeeding and runs `review-commit` (Codex, then the verdict); `pr-fixer-refix` fires on `request_changes` and runs `pr-fix` again with the findings; `pr-fixer-publish` fires on an approval with a passing gate and runs `publish-fix` (push, threads, replies). Disabling `pr-fixer-publish` gives a review-only mode in which the review posts its verdict. Only fix runs count toward the PR's attempt budget. The review stage is named `pr-fixer-review-commit` (d21's text says `pr-fixer-review`, which already names the review-submitted trigger rule). The 0.13.0 single workflow moves to a test fixture and stays trusted for the transition. Rollout order: docs/operations/pr-fixer.md, "Rolling out the split".
- Review records per commit (d21): each outcome stays an immutable record in `fixer_reviews`; one that names a whole target (repo, PR, base, start, tip; `review_target`) moves that commit's forward-only pointer in `fixer_review_targets`. The per-run `fixer_review_current` pointers are no longer read.
- Chain verification (d21): the `review` builtin in a `review-commit` run walks to the `pr-fix` run whose success started it, verified from the store (`culture_rules/actors/lineage.py`: the run is its rule's firing on that run's genuine event, equal to its completion record; the upstream run is a trusted `pr-fix` that succeeded), and judges that run's last gate and implementer. `github.push` verifies the whole chain before anything else: trusted workflows in their roles, verified lineage, the commit, start, bundle, repo, PR and branch exactly as the fix run's gate built them, a passing gate, every rule of the chain enabled, and an approval written by the chain's own review run. New refusals: `chain_unverified`, `chain_mismatch`, `review_not_in_chain`; a request for changes with the attempt budget spent ends the chain with `changes_requested` and the findings.
- Workflow trust by role (d21): `TRUSTED_WORKFLOWS` pins digests per role (`pr-fixer`, `pr-fix`, `review-commit`, `publish-fix`); a run may do only what its role allows.
- Chain holds (d21): a keyed run whose event a live rule would continue holds its key at its end, in the terminal transaction, so only the continuation is admitted; a fresh firing is deduplicated and fires when the chain ends. A continuation that does not take the key releases it; a 15-minute TTL is the backstop; a restore drops a hold with its reservation.
- `only_at_chain_end` on an action or `on_failure` (d21): skipped (`chain_continues`) while a live rule would continue the run's chain, so a chain posts one comment. `rules describe` adds `(only where its chain ends)`; the editor shows it as a checkbox, and the run-event trigger picker asks for a condition on the run's workflow.
- Comment intent (d21): start the comment with `/fix` or `@rules-culture-dev`. PR comments, reviews and review comments carry `command` (`/fix`) and `mention` (`@<app slug>`), read from the body's first token only (ASCII case-insensitive, a real token boundary; `/fix` needs whitespace or the end after it, so `/fix, please` is ignored); anything later in the body (a mention mid-sentence, a quote, a code block) never counts. The fixer's comment rules start a run only for a form listed in the new shared variable `fixer_comment_triggers` (default `["/fix", "@rules-culture-dev"]`); Qodo's billing notice, a status note and a closing comment start nothing.
- Every PR-scoped GitHub event carries the PR's `state`; every fixer trigger rule requires `state == "open"`.
- `require_commit` on an agent step (d21): a bridge turn that leaves no commit fails the step at once (`no_changes`), so the attempt ends before any gate or review and hands back once.
- The node cancels bridge jobs whose step attempt is over (timed out, superseded, failed, or its run failed or was cancelled) through the bridge's `POST /v1/invocations/<id>/cancel`, from the actor's machine, at most 5 tries (`cancel_sent_at`, `cancel_reason`).
- The `sonar.gate_issues` builtin (`culture_rules/node/actions/sonar.py`, client `culture_rules/apps/sonarcloud.py`): the agent gets only the issues behind the PR's failing SonarCloud quality-gate conditions (bugs, vulnerabilities, code smells, hotspots), at most 50, with a note; a failed lookup is advisory, never a failed run.
- Run events (d21, engine phase): every finished run emits exactly one `rules.run.succeeded` / `.failed` / `.cancelled` / `.superseded` event, carrying the run, rule, workflow, status, concurrency key, only the explicitly exported outputs, the error code and the trigger's PR fields, with causation/correlation lineage. The event is built once in the terminal transition and stored in the same transaction as an immutable completion record (`run_completions`); an outbox delivers un-emitted records exactly once. Rules fire on them with an ordinary `event` trigger and conditions over `trigger.data.*`; the editor's trigger picker offers them under "Rules engine (a run finished)". An event that does not equal its completion record is refused (`run_event_unverified`); ingest and the webhook sink quarantine envelopes in the engine's namespace (`event_quarantine`). See docs/run-events.md.
- Event hop limit (d21): derived events carry `hops`; a firing on an event more than 8 derivations from an external one (or with a malformed count, or none on a derived internal event) is refused as the recorded skip `hop_limit`, so rules firing on each other's runs stop.
- Change-feed consumers initialise their cursor once, by insert, so two nodes starting together never skip changes between their two heads.
- Backups carry the run-event decision state with run history (consumption marks, decision records, firing intents, key reservations, runs, completions, in that scan order); a chain without these tokens takes a new snapshot first. A restore runs a reconciliation pass before any node starts: it re-opens every emitted completion whose event is missing (keyset-paged) for re-delivery under its assigned (immutable) event id, drops orphan key reservations (refunding a failed start's attempt), and re-drives finished runs, final skip decisions and failed intents whose dependants were not decided yet, with every chain cursor pinned first (decision records now carry a bounded trigger snapshot; without one the trigger is recovered from a run or intent of the rule or its predecessors, transitively, else the continuation is recorded in `chain_needs_review`, left unhandled for a retry that resolves it, and counted by `health_status`); the remaining limits are listed in docs/operations/backup.md. A refused run event is never marked consumed. A record whose event id is taken is parked with backoff and read due-first; legacy records without `blocked` are migrated. Quarantine records are bounded (one per id and reason, counted, 8 KiB payload and 256-byte field caps, a final 16 KiB check, 30-day TTL installed by every node and the API); a webhook carrying a reserved type answers `quarantined`.
- Rule field `counts_toward_budget` (default true; d21): false keeps the concurrency key's one active run but neither counts the rule's runs toward nor refuses them by the attempt budget. It needs a `concurrency_key` and cannot be combined with `max_attempts`; `rules describe` reads `outside the attempt budget`.

- Fixes from Codex's review of phase 2:
  - every node releases chain holds past their TTL, so the pending event fires; a late continuation of an expired or released hold keeps the pending event instead of coalescing it away;
  - `github.push` follows a re-fix's verified lineage back to the run an external event started, and every rule along it must be enabled (disabling the initiating trigger rule stops the push);
  - a single-workflow run approved by the 0.13.0 build (per-run pointer, record without a target) is judged and consumed through a narrow legacy path, so in-flight runs survive the upgrade;
  - comment intent reads only the comment's first token, so no Markdown (fences, indented or inline code, quotes, lists) can smuggle in an ask;
  - `sonar.gate_issues` pages hotspots and issues up to the cap, reports `total` and `omitted` across every type, sets `truncated` from them, and its note names "the first N of TOTAL".

- The event hop count crosses the events-cli bus inside `data` (`_culture_rules_hops`): events-cli 0.10.0's envelope has no `hops` field and refuses unknown ones, so every caused emission (human asks) failed to publish on a node with the `events` extra. The node's sink moves it in, its source moves it back on engine-sourced events.

### Changed

- `github.push` reports a nothing-to-push commit as done only after reading the PR as the App: a closed PR is `pr_not_open`, never a success.
- Describe vocabulary: the review verdict reads "recorded for its commit (github.push checks it)", the push "(only on ... an approving review of exactly that commit)", an agent with `require_commit` "must commit".
- d21 on top of the 0.13.0 cognitive-complexity pass (Sonar S3776): d21's additions live in that pass's helpers, and the ten d21 functions over 15 (bridge orphan cancel, final-gate lookup, chain-hold decline, decision settle, completion re-open, envelope derive, hook sink, rule describe, trigger recovery and the restore re-drive) are split into named helpers, each check, error and write in its old order; characterization tests pin the branches that were uncovered.

## [0.13.1] - 2026-10-08

### Fixed

- Variables editor: empty items (null or "") no longer make a list mixed, so an edited null in a number list saves a number (5, not "5"), an edited empty item in a boolean list must be true or false, and non-numeric text there is refused

## [0.13.0] - 2026-10-07

### Added

- PR fixer (plan pr-fixer-rule): four disabled-by-default rules sharing one `pr-fixer` workflow, shipped as importable data in docs/rules/pr-fixer/ with a variable seed script. Settled red checks or a trusted comment or review starts a wait, then an agent on the cultureagent Qwen bridge, a test gate with a diff guard, and a GitHub App push. Review threads are replied to and resolved, and the run link is posted. The rules fire only for repos in vars.fixer_repos.
- Shared variables: a fifth editor tab (Variables), `vars.<name>` in conditions and `$var` inputs, the CLI/API/MCP `variables` noun with list `add`/`remove`, and fail-closed evaluation. Saving, enabling, importing or restoring a variable rule is refused while an online node lacks the capability.
- Engine: `wait` steps (quiet period, head_unchanged guard, superseded runs), `retry_until` with `carry`, built-in code steps (`gate`, `action`, `github.threads`, `github.threads_addressed`), the rule `on_failure` action and `run.id`/`run.error.*` references.
- Per-PR concurrency keys with a shared attempt budget (max_attempts, reset on a human push or green checks, coalescing of deduplicated events). Runs can be cancelled when a rule is disabled: `rules stop-runs` and an editor prompt.
- GitHub: once-per-SHA checks settle (`github.pr.checks_settled`, recovered from stored completions, polled only on a node that can serve the App), PR facts (head/base repo, branch, SHA, draft, author) on every fixer trigger, PR-comment enrichment, and the `github.push` and `github.review_reply` action kinds.
- Bridge actors (cultureagent bridge protocol) with callbacks on the API, plus the test gate run as a separate OS user and the spark2 fixer install scripts and ops recipe (docs/operations/pr-fixer.md).
- Plain descriptions (d19): `rules describe` / `workflows describe` (CLI, MCP, `GET /rules/{id}/describe`, `GET /workflows/{id}/describe`) render a rule as `When / If / Run / On / Then / On failure / Key` lines and a workflow as numbered steps, built only from the config by a fixed vocabulary (`culture_rules/model/describe.py`, no AI). The editor shows them behind an (i) "About" button on every rule and workflow (list rows and the open rule's or workflow's title): a non-modal, keyboard-operable panel with the same lines in mono, and Copy.
- Agent review before every fixer push (d20): the `pr-fixer` loop is now agent, gate, an independent reviewer (`codex-reviewer`: Codex through the cultureagent codex bridge on spark, sandbox `read-only`, shipped as docs/rules/pr-fixer/actors/codex-reviewer.json) and a built-in `review` verdict step. The reviewer gets the gate's verified diff of the fix and returns `approve` or `request_changes` with findings; it runs only after a passing gate (or no gate). Requested changes become the next attempt's instruction within the same 3 tries; after three, the run hands back with the last findings. The verdict is parsed fail-closed (anything malformed, ambiguous, for another commit, or from a reviewer that wrote, is not read-only or is the implementer's actor or backend fails the run).
- `github.push` refuses every push unless the run's review record approves exactly that commit by a reviewer whose actor and backend differ from the implementer's (`review_missing`, `review_rejected`, `review_commit_mismatch`, `reviewer_is_implementer`). The check reads the store, never a param, so a workflow edited to skip the review pushes nothing.
- Engine: a step's `config.when` (a condition over its inputs) skips it when false; a `retry_until` loop's `config.explain` adds the last attempt's text to `loop_max_exceeded`. The gate reports its verified diff (`diff`, `diff_chars`, `diff_truncated`, cap `diff_max_chars`). A bridge actor with `sandbox: read-only` cannot be widened by a step (`sandbox_locked`), and `max_bound_input_chars` refuses inputs the bridge would cut (`bound_inputs_too_large`).
- Hardening of the d20 review after Codex's adversarial review: the gate builds and pushes ONE commit itself (the agent tip's tree on the PR head), so intermediate agent commits never leave spark2, and merges are guarded (`merge_commit`); binary files, mode changes, symlinks and submodule pointers make the diff incomplete (`diff_problems`), so Codex never runs and the try asks for a text-only fix; the reviewer brief lives in code and is locked on the actor (`instruction_locked`, digest checked); the reviewer must be an actor flagged `reviewer: true` on codex (`reviewer_not_allowed`) and the implementer is the agent step that made the gated commit; review records are immutable per attempt with a forward-only current pointer, bound to repo, PR, start and tip (`review_target_mismatch`), and `github.push` re-checks the approval right before `git push` (`review_changed`).
- Second hardening round (Codex): only a workflow whose definition digest is pinned in code (`TRUSTED_WORKFLOW_DIGESTS`) can push or record a review (`workflow_not_trusted`); the gate-built commit carries only engine-written metadata (App bot author and committer, an engine message, the PR head's dates); a diff that is not valid UTF-8 is incomplete; thread replies name the pushed commit; review record ids include the verdict step key and conflicting results for one try fail closed (`review_conflict`); `github.push` consumes the approval by compare-and-set right before `git push`, after which no verdict can revoke it (`review_consumed`).
- Third hardening round (Codex): the security fields of `codex-reviewer` and `github-app` are pinned as digests in code (`TRUSTED_ACTOR_DIGESTS`; `actor_not_trusted`; the `github-app` digest is the live actor's, its `repos` scope deliberately excluded; `python -m culture_rules.actors.trusted` prints digests); the gate builds the published commit first and tests exactly it; it checks `base_sha` against the PR's base read as the App (`base_mismatch`, `base_unverified`) and the record keeps the policy commit; identical verdict replays no longer read as conflicts, and a clash before the first pointer is a conflict.
- Fourth hardening round (Codex): trust is checked on the configuration that executes - the bridge adapter refuses to dispatch a pinned actor from an untrusted snapshot and records the digest and endpoint it used, the verdict requires that recorded digest, and `github.push` checks and uses one actor snapshot; `github.push` refuses `base_changed` when the PR's base differs from the reviewed one.
- Fifth round (Codex, robustness): `base_changed` re-arms the head's checks settle in a new generation (`rearm_settle`, at most `REARM_LIMIT` per head), so a fresh run gates and reviews against the new base; the actor router refuses soft-deleted actors before model conversion and adapters get the raw stored document as their security snapshot.
- Re-arm hardening (Codex confirmation pass): the settle's poll claim and completion compare-and-set include the generation; a re-arm is bound to the refused review record (`replayed` on a replay); a head armed by a refusal keeps the PR number and branch; the agent factory requires the raw stored document.
- Workflow canvas zoom (d19): pinch or ctrl/cmd + wheel, Zoom out / Zoom in / Fit to width buttons and `+` / `-` / `0` keys, 25%–200%. A plain wheel still scrolls the page, and button zooms do not animate under prefers-reduced-motion.

### Changed

- Replay previews evaluate shared variables like live matching.
- Resumed action steps keep the placement derived from their actor.
- SonarCloud code smells fixed on #17: composite test assertions split (S9073), one throwing call per `pytest.raises` (S5778), constants for duplicated literals (S1192), narrower or re-raised exception handlers (S5713, S5754), unused private parameters removed (S1172), a linear commit-identity regex (S8786), and the (i) About panel and canvas zoom group use native `<dialog>` / `<fieldset>` (S6819, S6848) with the same look and keyboard behaviour.
- SonarCloud cognitive complexity (S3776) on #17: 41 functions over 15 (the run engine's wait wakes, timers and settle, the checks settle re-arm, the bridge callback recorder, the review record and verdict, the gate's diff guard, firing, the GitHub push and webhook paths, the model checks, the CLI renderer and three web functions) split into named helpers, each check, error and write in its old order; characterization tests pin the branches that were uncovered.

### Fixed

- Engine: time queued behind a busy actor no longer eats a step's working time. A `blocked` step (actor at its concurrency cap) gets its full `timeout_s` from the dispatch the actor accepts, not from the first, blocked dispatch (live: the pr-fixer agent accepted at 17:40 timed out at 17:52 on a deadline set at 16:47). Waiting has its own bound, twice `timeout_s` from the first `blocked` (`QUEUE_LIMIT_FACTOR`), after which the step fails `queue_timeout` (replaces `blocked_timeout`, which consumed an attempt and queued again). The bound also holds while a re-poll left the step `pending` on a drained or unavailable node, never once it is `dispatching` (that work may have started).
- Engine: a queued step is re-asked with a backoff (5, 10, 20, 40, then every 60 s) instead of every 5 s from any node, and its run history records the spell once (`dispatched`, `blocked`, then its outcome); repeat polls only move `rev` and update the step's `queue` record (`since`, `deadline`, `polls`, `last_at`, `left_at`), so a long queue cannot grow the run document toward MongoDB's 16 MB cap. A guarded wake whose head lookup is `blocked` backs off the same way, records `wait_blocked` once, and fails `queue_timeout` past the same bound (twice the wait step's `timeout_s`, default 3600 s), checked in housekeeping on any node, drained included, so an expired wait never looks up the head again. On upgrade, a step (or guarded wake) queued by the previous engine, which has no queue record, is adopted (`queue_adopted`): its spell is dated from its old deadline minus `timeout_s` (the first blocked dispatch), else its first `blocked` / `wait_blocked` history entry, and one already past the bound fails `queue_timeout` without being asked again.

- The node install's default extras include `yaml`, so the test gate can read a repo's `culture.yaml` (the first live gate failed with `extra_missing`).

- The node unit no longer blocks a sudo gate run-as: `deploy/node/install.sh --gate-run-as PREFIX` writes `CULTURE_RULES_GATE_RUN_AS` into node.env (kept across reinstalls) and, for a sudo prefix, `NoNewPrivileges=false`. A node that starts with a sudo prefix under no_new_privs logs an error naming the fix, and the gate refuses `run_as_blocked`.
- The gate's setup and test commands no longer see the run-as account's name in their temp paths (lobes-cli#302). They run with `TMPDIR` and pytest's `--basetemp` (through `PYTEST_ADDOPTS`, so a repo's own `--basetemp` still wins) inside the gate's workspace. The workspace is now `culture_rules_gate.XXXXXXXXXX/` holding `checkout/` and `tmp/`, and is removed whole. Under the default basetemp, every `tmp_path` held `pytest-of-culture-fixer`, so a lobes-cli test asserting `-f` was absent from a dry-run output failed only in the gate.
- The gate no longer hides run-as errors: a failed worktree pack carries a bounded stderr tail, and a probe tells `run_as_failed`/`run_as_blocked` (the prefix itself cannot run) from `source_unavailable` (a missing commit).

## [0.12.1] - 2026-10-07

### Added

- `culture.yaml` declares the PR fixer test gate (`uv sync`, then `uv run pytest -n auto`). The gate is read from a PR's base commit, so it must be on `main` before the fixer can push to culture-rules PRs.

## [0.12.0] - 2026-10-05

### Added

- `discord.message` action ("Post a message on Discord"): actor, server and channel picked from what the bot can see; the channel can be mapped from the trigger to reply in place
- `GET /actors/{id}/discord/targets` and `culture-rules actors discord-targets <id>` (CLI and MCP): the servers and text channels a Discord app actor can post to, with private channels the bot was not added to marked

### Changed

- `message` is "Send a message on the mesh" only and takes no actor in the editor; a stored `message` naming a Discord actor still posts to Discord and is shown as a Discord message
- Action picker mapping fields use the real event data names (`repository`, `key`, `project`, `channel_id`, `content`, ...)
- docs/actors/discord.md: posting a message; the API needs the bot token injected for the channel list

## [0.11.4] - 2026-10-05

### Changed

- Delivery validation for the second mile filed in devague: 13 obligations on c32, c35, c41-c46; evidence e1-e14 (13 pass, 1 fail: one typeless event rule remains after migrate-typeless); behavioral deltas b1-b4 (injected hidden secrets, actor-machine placement, Access bypass as path apps, migrate-typeless disables rather than reaching zero)

## [0.11.3] - 2026-10-05

### Added

- `deploy/node/install.sh --secret NAME` (repeatable) injects grant secrets into the node unit
- `docs/actors/`: one setup guide per app actor (GitHub, Discord, Jira): creating or inviting the app, sealing secrets, injecting them, the actor definition, events, verification and troubleshooting
- Ops doc: injected app secrets, actor pinning, the two path-scoped Access bypass apps, the Jira gateway base

### Fixed

- App secrets work when sealed `--hidden`: a `grant:NAME` reference resolves first to `CULTURE_RULES_SECRET_<NAME>`, which the service unit injects with `grant run --inject` (as it already does for the Mongo URI); `grant get` refuses hidden secrets, so GitHub, Discord and Jira app actors could not authenticate
- A rule action through an actor that lives on a machine runs on that machine (where its secrets are injected), and only that machine's node holds the actor's Discord gateway lease
- `deploy/node/install.sh` keeps the installed MongoDB CA on an upgrade without `--ca-file` (it used to drop the line from node.env)
- CLI: a server-side dry-run (the migrations) no longer prints `would None None`

## [0.11.2] - 2026-10-04

### Fixed

- Node: one malformed rule document (or a stored `cron: null`) no longer stops the schedule, probe, firing or chain stage for every other rule on the host; it is logged and skipped
- Events: a non-JSON or legacy fan-in cursor no longer wedges ingest when events are waiting, and an idle reset is persisted so the warning is logged once
- GitHub App actions: a revoked installation token is re-exchanged once on 401; repo shape and App ids are checked before the private key is read; a deleted or disabled actor drops its cached key
- Message action: a Discord or mesh timeout is an unknown outcome and is not retried (a retry could post twice); a single-string `connection.channels` is one channel
- Editor: guided messages for the trigger/action validation codes, `not_implemented`, `replay_invalid` and `not_json`

## [0.11.1] - 2026-10-04

### Changed

- Ops doc: the migrations (rules migrate-typeless, runs backfill-ids) are merged and part of the rollout; the Jira webhook token is sealed as RULES_JIRA_WEBHOOK_TOKEN

## [0.11.0] - 2026-10-04

### Added

- Typed triggers (#5): event triggers require params.type (trigger_type_required); schedule triggers (stdlib 5-field cron, UTC or an IANA tz, DST-safe, one run per slot on the placed host, no backfill); probe triggers run an allow-listed runner command on a cron and fire on change or on a condition over the output
- App actors (kind app) for GitHub, Jira and Discord: declared events, probes and actions, connection secrets as grant:NAME references only
- Webhook receivers POST /hooks/github (X-Hub-Signature-256) and POST /hooks/jira (HMAC or URL token, issues refetched by key), exempt from Access/auth by exact path only, deduped on delivery id, answering before any rule runs; query strings are stripped from access logs
- Discord Gateway listener held by one node mesh-wide under a named lease (`discord-gateway:<actor>`), writing discord.message.created events; discord extra
- Action kinds message (Discord or mesh), github.comment (as a GitHub App; github extra), jira.comment, http.call (destination allowlist, private/tailnet ranges refused, pinned address, no redirects) and machine.command (runner CodeRunner with typed args), registered as node action ports
- Rule actions dispatch through the actor named in params.actor with that actor's limits; an unknown or disabled actor fails the run with actor_unavailable
- Self-authored events (our App, bot or service account) do not fire rules unless the trigger sets include_self; per-rule fire-rate cap trigger.params.max_fires_per_hour (default 60, schedule triggers uncapped) records rate_capped skips
- Direct workflow runs: POST /workflows/{id}/run and `workflows run` (CLI and MCP) with typed inputs validated against the declared ports, pinning a synthetic `adhoc:<workflow>` rule
- A human actor is created on first Access sign-in (id from the email, or linked to an existing human with the same params.email)
- `actors enrol-agents`: enrols the mesh agents listed in the Culture server manifest (server.yaml in the per-user Culture directory) as agent actors for this machine, disabling (never deleting) ones no longer listed
- `rules migrate-typeless` and `runs backfill-ids` (admin, dry-run by default)
- /health reports webhook delivery outcomes per actor and the Discord gateway holder and state
- Editor: typed trigger picker and action picker with mapping chips; Actors tab app connection, declarations and runner command editors; Workflows tab run form with typed inputs and outputs in place, full step properties, selectable in/out nodes with inputs, outputs, variables and description editors, deleted-workflow view with admin purge, guided errors with fix options
- deploy/node/install.sh (dry-run by default, offline wheelhouse) and ops docs for the cache rule, hook Bypass paths, GitHub App, Discord bot, Jira webhook, kill switches and the four-node upgrade order
- Optional extras github (cryptography) and discord (discord.py); every module imports with them absent

### Changed

- Run docs carry top-level rule_id and workflow_id; rule history and run lists filter in the store
- Heartbeat online threshold derives from the beat cadence (offline_after); a missing heartbeat counts as offline, an unreadable one as online
- WorkflowRef.inputs accepts {"$ref"} and {"$literal"} forms
- The chain feed skips its shared transaction when no rule depends on the finished rule; a legacy events cursor starts fresh; event subscription depth is configurable (CULTURE_RULES_EVENTS_DEPTH)
- Switches are disabled while a toggle is in flight

### Fixed

- An event trigger with no type no longer matches every event
- A predecessor decision written final in one step on another host now cascades to its dependants
- Tall workflows no longer overflow the canvas; the workflow list dot resolves actor placement to its machine

## [0.10.5] - 2026-10-03

### Added

- Action params accept explicit {"$ref": path} and {"$literal": value} forms; a $ref that can never resolve is refused at save (422 invalid_reference)
- Each node cycle redelivers human ask answers that were recorded but not delivered (e.g. a crash in between); `node run --once --json` reports a `redelivered` count
- docs/operations/pause.md: what a pause holds back and what it drops

### Changed

- Colleague, mesh and runner actors no longer claim cross-process idempotency: an attempt whose outcome is unknown (crash, resume, lost ack, timeout) now fails with unsafe_retry instead of running again; declare the step idempotent to retry automatically
- Adding, changing or removing a runner actor's params.commands is admin-only on create, update and import (403 runner_commands_admin_only)
- Registered runner commands that evaluate inline code (sh -c, python -c, node -e, perl -e, ... also behind env/sudo wrappers) are refused at run time
- `matches` refuses catastrophic-backtracking patterns at save (422) and treats a stored one as a recorded non-match; its input cap drops from 10,000 to 2,000 characters
- `!=` on a missing field is now false like every other comparison; write !(a == b) to match when the field is absent
- A retry after a failed attempt counts against the actor's concurrency cap and token budget
- Plain strings resolve as references only when their path fits a namespace (trigger envelope fields or event keys, `workflow.outputs.*`, `rules.<id>.outputs.*`); others, like `rules.yaml` or `trigger.sh`, stay literal

### Fixed

- A step's claim lease is renewed while its actor runs, and a lapsed claim is taken over only when the holder's heartbeat is stale or the step's deadline passed, so a long step no longer runs twice
- Engine nodes beat from their own thread, so a long step no longer makes its host look offline
- Answering a human ask frees the human actor's concurrency slot at once (it stayed held until the step deadline)
- Actor adapters no longer cache failed results, so retry policies re-run the work
- A predecessor settling during an engine pause no longer turns a waiting must/may-run-after dependant into a final skip; it is re-evaluated on resume
- Literal strings such as trigger.sh in workflow step config are no longer reported as trigger references; {"$ref": "trigger..."} in a workflow is
- The inline-code guard for runner commands strips interpreter version suffixes without a regex, so a hostile 10k-character argument is classified in linear time

## [0.10.4] - 2026-10-03

### Added

- Workflows tab: a left-pane list of every workflow (New workflow, machine dot, name, enable switch), aligned with the Rules list
- Rules tab: the create button reads "New rule" (it opens the "When does this happen?" form), like "New workflow"

### Changed

- Cleared every SonarCloud maintainability issue on the PR (508) SonarCloud maintainability issues: split composite test assertions and pytest.raises blocks, read-only React props, native fieldset/output/dialog elements instead of ARIA roles, and 33 cognitive-complexity splits in culture_rules and web with no behaviour change
- `Node` takes its probe/load-reader/engine-version/beat-interval settings as one `HeartbeatOptions` (14 -> 11 parameters)
- The workflow import reads files with `Blob#text` only (the FileReader fallback was dead code for every supported browser); the logo SVG is decorative and named by visually hidden text

### Fixed

- The multi-host chaos test no longer times out when the killed host never wins an action race (harness holds the survivors per run advance until the victim dies); exactly-once and no-loss held throughout
- A WorkflowsCreate test that raced the router on slower CI runners

## [0.10.3] - 2026-10-03

### Added

- Workflows tab: New workflow, in the head next to Import and as the empty state's primary action. It asks only for a name (Enter creates, Escape cancels), creates the workflow with POST /workflows (no steps; an id held by a live or soft-deleted workflow moves on to -2, -3, ...) and opens it on the canvas with the step + focused. API errors show inline in the form.

### Fixed

- Editor parity for workflows (spec: every workflow operation reachable from the editor, the CLI and the MCP server): the Workflows tab had no create, rename, enable/disable or delete. It now renames the workflow (a draft edit written by Save), enables and disables it (POST /workflows/{id}/enable|disable), and deletes it softly with Undo (DELETE, then POST /workflows/{id}/restore); deleting a workflow a rule still uses keeps it and names the conflict.

## [0.10.2] - 2026-10-03

### Fixed

- Every API response now carries Cache-Control: no-store (API reads, 401/403 envelopes, unknown API paths, the event stream); the HTML shell is private, no-cache and content-hashed /assets are private, immutable. Cloudflare was serving a 15-minute-old /api/machines from its edge cache, so newly enrolled machines were missing from Statistics, and a shared cache could hand one principal's answer to another.

## [0.10.1] - 2026-10-03

### Fixed

- `culture-rules node run` no longer dies at startup against the real events-cli: events-cli rejects the raw MQTT filter `#` and has no catch-all pattern, so the node now registers one durable subscription per event-type depth (`*`, `*.*`, ... up to 4 segments) and drains them as one source. Any events-cli setup failure (rejected subscription, unreachable broker) now degrades the node to running without ingest instead of crashing it, and broker drains use a positive timeout (events-cli rejects 0).
- `/health` reports the node named by the new `CULTURE_RULES_NODE_NAME` (or `serve --node-name`) instead of `socket.gethostname()`; `node run` defaults `--host` to the same variable before the short hostname.
- `culture-rules mcp` without the `mcp` extra exits 2 with the install hint instead of 1 (anyio/mcp.server.stdio imports now raise `ServerExtraMissing`).
- `culture-rules serve` with a missing or unreachable store exits 2 naming `CULTURE_RULES_MONGO_URI` and `pip install 'culture-rules[store]'` instead of 1; under `--json` stderr holds only the JSON error.
- The web Statistics `#agent-state` test asserts `status` reaches `ready` instead of an always-true check.

## [0.10.0] - 2026-10-03

### Added

- Rules engine library `culture_rules`: typed model + JSON Schemas (rule, workflow, action, actor, machine, placement), a dependency-free condition evaluator, rule matching (all-fire, exclusive groups, supersede, must/may-after with explicit exports), placement by machine, actor or requirement, exactly-once claims, a persisted run executor with pinned versions, retries, bounded loops and pause/drain/cancel, append-only audit and soft delete/restore/purge.
- Storage port with an in-memory adapter and a MongoDB replica-set adapter (majority writes, change streams, TLS/auth, typed transient errors); deploy artifacts and runbook for a spark/thor/orin replica set with spark2 non-voting.
- Actors: agent (colleague work --json, correlation-matched mesh tasks), code runner (registered argv commands, admin-only sandboxed inline scripts), human asks (id-bearing event, exactly-once answer), per-actor budgets and concurrency caps, grant: secret references.
- Engine node daemon `culture-rules node run`: heartbeat with platform probe, events ingest, placed and shared trigger consumers, rule chaining re-evaluation, executor loop, run summaries.
- HTTP API under the optional `server` extra with a committed `api/openapi.json`, Cloudflare Access JWT on a loopback listener plus service tokens on the LAN listener, viewer/editor/admin roles, SSE live updates, replay, rule history, repo-backed export/import.
- CLI noun groups (rules, workflows, actors, machines, runs) over the API from one command registry, dry-run by default with --apply; `serve`, `node` and `mcp` verbs; MCP server under the optional `mcp` extra.
- Web editor (Vite + React + @xyflow/react) with four tabs — Rules, Workflows, Actors, Statistics — following the chosen design canvas; shipped inside the wheel and served by the API.
- Encrypted, versioned S3 backups with a restore drill (`backup` extra); cloudflared/rules.culture.dev runbook; observability (health, JSON logs, run-id propagation).
- Delivery artifacts: spec, plan, split plan and delivery summary under docs/; multi-host chaos tests; surface parity test; CI web job (typecheck, vitest, palette validation, Playwright, webglass) and a 60% coverage floor.

### Changed

- README, CLAUDE.md and the harness prompt files describe the shipped four-tab design and the CLI/MCP/API/node surface instead of a scaffold.

## [0.9.1] - 2026-10-02

### Changed

- `CLAUDE.md` expanded from the bootstrap seed into the full runtime prompt via `/init`, grounded in build brief #1 and product-model/UX issue #2: domain model (rule/condition/workflow/action/actor), settled constraints, planned shape, build pitfalls, exact CI commands, CLI contract, harness editing rules, worktree and memory conventions.
- `README.md` rewritten to describe the rules engine and its three-tab editor (planned) instead of the template; skill count corrected (19, not 11); template-only "Make it your own" section removed.
- `QWEN.md`, `AGENTS.override.md` and `AGENTS.colleague.md` now describe culture-rules (status: scaffold) rather than "a clonable template", and no longer point at the removed "Cloning this template" section of `CLAUDE.md`.

## [0.9.0] - 2026-09-06

### Added

- **Four agent harnesses, each reading exactly one root file.** Claude
  Code→`CLAUDE.md`; Pi/`associate`→`AGENTS.override.md` (context) plus
  `.pi/SYSTEM.md` (system prompt, which *replaces* Pi's default);
  colleague→`AGENTS.colleague.md`; Qwen Code→`QWEN.md`. Deliberately **no**
  `AGENTS.md` — `AGENTS.override.md` is what stops Pi inheriting `CLAUDE.md`.
- One skill tree, four loaders: `.qwen/skills`, `.colleague/skills` and
  `.pi/skills` are relative symlinks onto `.claude/skills`. No forked scripts,
  no duplicated docs.
- `docs/harness-selection.md` (the two selections), plus
  `docs/automation-contract.md` and `docs/harness-invocations.yaml` (the four
  forced invocations as a machine-readable contract), and
  `docs/harness-verification.md` (the instrumented run).
- `scripts/harness-smoke.py` — a per-harness CI check that fails when **any one**
  of the four configs is broken, so three-quarters of a clone cannot rot
  unnoticed. A skipped check is reported as not-verified, never as a pass.
- `scripts/scan-secrets.py` — CI gate against committed credentials and
  non-localhost endpoints, with planted-secret tests proving it catches.

### Changed

- `culture.yaml` declares `backend: claude`. Because no code path rewrites that
  key, the template's declaration is what every clone inherits — the previous
  `colleague` value is why ~30 siblings carry a backend disagreeing with their
  seeded prompt file.
- `backend-fingerprints.yaml` re-synced from steward: list-valued prompts, so
  `acp` accepts `QWEN.md` and `colleague` accepts `AGENTS.override.md` and
  `.pi/SYSTEM.md`.

### Fixed

- `CLAUDE.md`, `README.md`, `QWEN.md`, `AGENTS.override.md`,
  `AGENTS.colleague.md` and `docs/skill-sources.md` had claimed this repo was a
  colleague resident, contradicting `culture.yaml`. Every harness prompt now
  names `backend: claude` / `CLAUDE.md` as the mesh resident, while stating
  that its own harness stays interactively available regardless.
- `.pi/settings.json`'s `skills` key is **inert** — that key belongs to a
  `package.json` manifest, not `settings.json`. Syscall instrumentation on a
  fresh clone: settings alone opened **0** `SKILL.md`; the `.pi/skills` symlink
  opened **19**. Replaced with the symlink, settings file removed.
- `doctor` no longer accepts an interactive harness's prompt file as the mesh
  resident's. Four harnesses ride three backend names, so `AGENTS.override.md`,
  `.pi/SYSTEM.md` and `QWEN.md` are *recognized* under `colleague`/`acp` — but
  the Culture daemon reads exactly one file per backend, and a clone carrying
  only Pi's files under `backend: colleague` used to report healthy with no
  resident prompt at all. `prompt_file_present` now requires the resident
  prompt; the others are reported by a new `harness_prompts` info check.
- `scan-secrets.py` closes three evasions: values containing `@`/`:`/`%`/`=`/`?`
  are now matched in full instead of stopping at a base64-ish alphabet (a
  quoted JSON key is matched too, which it never was); the placeholder
  exemption is a whole-value judgement, so a high-entropy literal merely
  *containing* `fake`/`example` is still reported; and endpoint hosts are
  parsed with `urlsplit`, so a bracketed IPv6 authority such as
  `http://[2001:db8::1]:8080` can no longer slip past the localhost allowlist.
- `harness-smoke.py`: a live probe is satisfied by **stdout only** (stderr
  noise mentioning `yes` or a prompt filename no longer counts as an answer,
  and a yes/no probe must answer exactly `yes`); a nonzero exit from `steward
  doctor` or `guild create` can no longer reach a passing branch on
  success-shaped JSON; failure/skip/waiver diagnostics go to stderr, leaving
  stdout to results; and an unknown `--stage` exits 1 (user error) rather than
  argparse's 2 (reserved for environment failures).

Known gaps carried into this release (not a changelog category — recorded
here so the release is not read as claiming more than it delivers): colleague
loads **0 of 19** skills from the nested tree, blocked on two upstream defects
filed with a reproduction as
[`agentculture/colleague#494`](https://github.com/agentculture/colleague/issues/494)
(the template ships the correct shape regardless); and retrofitting
already-provisioned siblings is an explicit **non-goal**, so
`culture-rules#25` stays open for the existing fleet.

## [0.8.0] - 2026-09-05

### Added

- **`validate-delivery` skill** (origin `devague`, re-broadcast by
  `guildmaster`) — the validation leg between `/assign-to-workforce` and
  `/summarize-delivery`: run the confirmed plan's behavioral tests agent-side,
  then file obligations, evidence, and behavioral deltas. Record-only; the
  `devague` CLI never runs a test.
- **`scripts/` wrappers for the five prompt-only workflow skills** — `scope`,
  `challenge`, `deviate`, `validate-delivery`, `summarize-delivery`. Each
  forwards its arguments to the `devague` CLI verbatim, so upstream owns the
  surface. Every clone now ships a complete, convention-clean skill directory
  instead of inheriting the script-less shape.

### Changed

- **Re-synced all eight `devague`-origin workflow skills** — `scope`, `think`,
  `challenge`, `spec-to-plan`, `assign-to-workforce`, `deviate`,
  `validate-delivery`, `summarize-delivery` — from devague `0.24.1`
  (`ec15362`), matching guildmaster's canonical copies. Notable upstream
  content: `/scope` fans out to read-only exploration subagents at 5+ candidate
  surfaces, and `assign-to-workforce split-plan --write` persists a durable
  gate-2 record.
- **All eight `SKILL.md` files are now byte-verbatim with upstream.** devague
  ships `type: command` on all eight itself, so nothing is added to the
  frontmatter here. The only divergence is the five wrapper scripts, which are
  additions, not edits.
- **`docs/skill-sources.md` updated for the re-sync** — count corrected from
  seven skills to eight (`validate-delivery` had no row), all eight rows
  repointed to `../guildmaster/.claude/skills/`, and pins refreshed to devague
  `0.24.1`.
- **The 2026-07-15 "vendor directly from devague" divergence is superseded.**
  That decision existed to keep guildmaster's `scripts/*.sh` wrappers out of
  this repo. The wrappers are now wanted: without them a clone ships a
  `SKILL.md` with no sibling `scripts/` and fails a
  `test_skills_convention`-style gate (guildmaster#95). The old section is
  retained, marked superseded, with its stale re-sync recipe replaced — that
  recipe would have deleted the wrappers this release adds and skipped
  `validate-delivery` entirely.

### Fixed

- **`scope.sh` usage advertised an invalid claim kind** — `--kind non-goal`
  (hyphen) is rejected by `devague capture`, which accepts `non_goal`. Anyone
  copying the wrapper's usage line got `invalid choice: 'non-goal'`. Corrected
  in both this repo and guildmaster.

## [0.7.0] - 2026-08-24

### Added

- **`resume <task-id|last> [--detach]` verb** in `ask-colleague` — pick a cut / timed-out / SIGTERM'd run back up from its persisted artifact, continuing on the original `colleague/<id>` work branch.
- **Per-seat thinking effort** in `ask-colleague` — `--effort` (acting seat), `--seat-effort S=R` (any seat), `--role NAME` (colleague#416). Rule of thumb: `--effort off` for small well-specified briefs, default for ordinary work, `xhigh` for open-ended judgement.
- **Review diff front-loading** — `ask-colleague review` embeds a filtered, bounded diff directly in the prompt instead of relying on the colleague run to fetch it.

### Changed

- **`ask-colleague` re-vendored byte-verbatim from `agentculture/colleague` @ 1.63.0** (cite-don't-import) — all five files (`SKILL.md`, `scripts/ask-colleague.sh`, `prompts/{explore,review,write}.md`). Every repo scaffolded from this template (`guild create` instantiates it) shipped the Qwen3.6-era wrapper until now.
- **Default colleague model is `unsloth/Qwen3.8-27B-NVFP4`** (was the Qwen3.6 pin). The lobes gateway on `:8001` no longer serves 3.6, so the previous default only worked via colleague's auto-refresh warning path.
- **`docs/skill-sources.md` ledger row** for `ask-colleague` updated to the 1.63.0 sync (was `2026-06-12 (colleague 1.7.0, direct)`) and its verb list extended with `plan` / `resume` / the pilot verbs.

## [0.6.1] - 2026-07-20

### Added

- **Worktree location convention** in `CLAUDE.md` — every worktree you create
  by hand (workforce fan-out lanes, scratch checkouts) lives in
  `../.worktrees.culture-rules/<name>/`, one
  repo-named directory beside the checkout, replacing a shared `../worktrees/`
  folder. This workspace holds many sibling projects, so a generic shared
  folder accumulates orphaned trees from several repos at once with nothing
  indicating ownership — a stale-tree sweep can't tell a live lane from junk.
  Matches the convention already documented in sibling repo `reachy-mini-cli`.
  Adds branch-prefix guidance (scope the prefix to the work; plain `agent/*`
  collides with leftovers from earlier fan-outs and fails `git worktree add
  -b`), and notes that the vendored `assign-to-workforce` skill uses both the
  shared path *and* `agent/<task-id>` branches in its fan-out example — it is
  cited verbatim and must not be edited, so both are overridden when following
  it. Teardown guidance names `git worktree remove <path>` as the verb that
  actually deletes a worktree; `git worktree prune` only clears metadata for
  directories that are already gone. Tool-managed throwaways are explicitly
  out of scope: `ask-colleague`'s read-only verbs create a detached worktree
  under `${TMPDIR:-/tmp}` and reap it on an EXIT trap, so they never persist
  to need an owner.

## [0.6.0] - 2026-07-18

### Added

- **Four devague-origin skills re-vendored into `.claude/skills/`**
  (cite-don't-import), synced to the fixed devague source
  (devague#74/#75/#76):
  - `challenge` — a risk-scaled blind-spot discovery pass that runs between
    `/think` and `/spec-to-plan`, routing findings back through the existing
    deterministic moves as human-adjudicated proposals.
  - `scope` — the idea→scope leg that surveys the surfaces an idea touches
    before framing, seeding the Announcement Frame with provenance-backed
    boundary/non-goal/assumption claims.
  - `deviate` — stops an in-flight `assign-to-workforce` run when execution
    must diverge from the confirmed plan and records the divergence as a
    first-class, append-only deviation record.
  - `summarize-delivery` — closes the loop after an `assign-to-workforce`
    run with a planned-vs-actual accountability artifact.

  These four originate in `devague` and are re-broadcast via guildmaster; see
  `docs/skill-sources.md` for provenance.

## [0.5.0] - 2026-06-24

### Added

- **Memory-discipline "Conventions and workflow" section in `CLAUDE.md`** — a
  per-task *recall-before / remember-after* convention (scope localized to this
  repo's nick) so the vendored `remember` / `recall` skills are actually used,
  not just present: `/recall` before non-trivial work to build on prior
  decisions instead of re-deriving them, and `/remember` when a non-obvious
  decision, constraint, fix-and-why, or hard-won gotcha surfaces. The section
  documents this repo's memory as **in-repo and public** — records resolve to
  `<repo-root>/.eidetic/memory` (committed, team- and mesh-shared). Inserted
  idempotently (skipped if already present), slotted under an existing
  "Conventions and workflow" heading when one exists, else appended.

### Changed

- **Refreshed the `remember` + `recall` wrappers from eidetic-cli 0.10.0**
  (cite-don't-import) — picks up eidetic's **project-local store default**: the
  files backend now resolves per record by visibility — PUBLIC records inside a
  git repo go to `<repo-root>/.eidetic/memory` (committed, team-shared), PRIVATE
  records (or any record outside a repo) go to `$HOME/.eidetic/memory` (never
  committed), an explicit `EIDETIC_DATA_DIR` still wins, and recall reads both
  stores and merges. Also carries the 0.9.3 hardening (interactive-stdin guard,
  `help` as a search term, SIGPIPE-safe suffix parsing). **Recipe policy
  override (the wrappers here are NOT byte-verbatim):** the injected default
  visibility is flipped from eidetic's `private` to **`public`**, so a plain
  `/remember` lands the note in `./.eidetic/memory` in this repo, kept as part
  of the repo — pass `--visibility private` to route a record to `$HOME`
  instead. `remember` drives `eidetic remember` (idempotent upsert of one JSON
  record or an NDJSON batch on stdin); `recall` drives `eidetic recall` with
  four search modes (exact / approximate / keyword / hybrid). Each `SKILL.md` is
  localized only in the illustrative `--scope <nick>` examples (Provenance keeps
  "First-party to eidetic-cli"). Runtime dep: the `eidetic` CLI on PATH (else a
  local eidetic-cli checkout with `uv`) — **`eidetic >= 0.10.0`** for the
  in-repo routing; on an older CLI the public records still work but are stored
  in `$HOME/.eidetic/memory` instead of in-repo. Propagated by rollout-cli's
  `eidetic-memory` recipe.

## [0.4.0] - 2026-06-23

### Added

- **Vendored the `remember` + `recall` memory skills from eidetic-cli**
  (cite-don't-import) — the write/read halves of eidetic's shared
  `$HOME/.eidetic/memory` surface, so this agent (Claude and its colleague
  backend) can persist facts across sessions and recall them later, sharing
  one store.
  `remember` drives `eidetic remember` (idempotent upsert of one JSON record or
  an NDJSON batch on stdin, dedup by id + content hash); `recall` drives
  `eidetic recall` with four search modes — exact / approximate / keyword /
  hybrid — each hit carrying text, full provenance metadata, a relevance score,
  and a freshness signal. The `.sh` wrappers are byte-verbatim from eidetic-cli
  (their first-party origin); each `SKILL.md` is localized only in the
  illustrative `--scope <nick>` examples (Provenance keeps "First-party to
  eidetic-cli"). Both default to this agent's PRIVATE scope, reading the suffix
  from `culture.yaml`. Runtime dep: the `eidetic` CLI on PATH (else a local
  eidetic-cli checkout with `uv`). Propagated by rollout-cli's `eidetic-memory`
  recipe.

## [0.3.4] - 2026-06-20

### Fixed

- Identity docs and self-description strings still claimed `backend: claude`
  (prompt file `CLAUDE.md`), but this template was promoted to a colleague
  resident in #14/#15: `culture.yaml` declares `backend: colleague` (Qwen) with
  `AGENTS.colleague.md` as the resident prompt. Corrected the stale claim in
  `CLAUDE.md` (Identity section), `README.md`, `docs/skill-sources.md`, and the
  two CLI description strings (`overview` artifacts and `explain doctor`). The
  `doctor` backend→prompt-file mapping and the tests were already on
  `colleague`; this aligns the prose and self-description with them.

## [0.3.3] - 2026-06-20

### Fixed

- pyproject.toml: correct the `license` field and PyPI classifier from MIT to
  Apache-2.0 to match the `LICENSE` file. The README License section was already
  corrected in 0.3.2, but the package metadata was missed; the built wheel now
  reports `License-Expression: Apache-2.0`.

## [0.3.2] - 2026-06-18

### Added

- ask-colleague skill: `monitor`/`guide`/`stop` pilot verbs plus a `--watch`
  flag to dispatch, watch the live feed of, send mid-flight guidance to, and
  cooperatively stop a running colleague flight (re-vendored from colleague).

### Changed

- README: correct the License section from MIT to Apache 2.0 to match the
  `LICENSE` file.

## [0.3.1] - 2026-06-13

### Changed

- CLAUDE.md: add a convention to reach for the `ask-colleague` skill reflexively
  for explore/review/write/grade — read-only `review`/`explore` are always safe;
  side-effecting `write` needs the user's go-ahead.

## [0.3.0] - 2026-06-13

### Added

- AGENTS.colleague.md resident prompt file (backend colleague <-> AGENTS.colleague.md)

### Changed

- Promote agent identity to a colleague resident: culture.yaml backend
  claude -> colleague with a pinned model. The `doctor` backend-consistency
  map gains `colleague` -> AGENTS.colleague.md.

## [0.2.1] - 2026-06-12

### Changed

- **Re-vendored the `ask-colleague` skill from colleague (now 1.7.0, up from the
  0.39.2 sync)** — the wrapper had drifted multiple releases behind origin. Picks
  up the `clean` verb (reap stale/corrupt `colleague/*` branches + orphaned
  `.colleague/` artifacts a crashed run left behind), the `--json` flag on every
  verb (result JSON on stdout, diagnostics/digest on stderr), the
  `_colleague_via_uv` local-dev resolution that honors `--repo`, and the
  tri-state (0/1/2) exit-code contract. `scripts/ask-colleague.sh` + `prompts/`
  are byte-identical to the origin; `SKILL.md` diverges only in the one
  consumer-identifying Provenance clause (`culture-rules vendors from
  guildmaster`). `docs/skill-sources.md` sync row updated to
  `2026-06-12 (colleague 1.7.0, direct)`. Refs: colleague#183, #186.

## [0.2.0] - 2026-06-06

### Added

- **`ask-colleague` skill** (`.claude/skills/ask-colleague/`) — the first-party front door to the `colleague` CLI (the renamed `convertible`). On top of `explore` / `review` / `write` it adds a `feedback` verb (grade a finished work item — the ROI loop), and `write` now **previews by default** in a throwaway worktree (no side effects) unless `--apply` / `--pr` is given. Reach for it reflexively — `review` for a diverse second opinion on a committed diff before opening a PR, `explore` for a fresh read of an unfamiliar area.

### Changed

- **Replaced the `outsource` skill with `ask-colleague`.** `outsource` was renamed to `ask-colleague` upstream ([colleague#148](https://github.com/agentculture/colleague/pull/148)). Because guildmaster has not re-broadcast the rename yet (its kit still ships the old `outsource`), `ask-colleague` is vendored **directly from the sibling `colleague` checkout** rather than from guildmaster — a tracked local divergence recorded in `docs/skill-sources.md`, parallel to the `agex` → `devex` one. Vendored verbatim except one consumer-identifying clause in the Provenance paragraph.
- **Ledger + CLAUDE.md + `.gitignore`:** point `docs/skill-sources.md` and the CLAUDE.md Skills section at `colleague` / `ask-colleague`, swap the *optional* runtime prerequisite `convertible` → `colleague` (env prefix `CONVERTIBLE_*` → `COLLEAGUE_*`, with the legacy names kept as a deprecated fallback), and gitignore the `.colleague/` run-artifact dir the skill writes (plus the stale `.agex/`).

## [0.1.4] - 2026-05-31

### Added

- **Vendor the `outsource` skill** (`.claude/skills/outsource/`) from
  guildmaster's canonical copy (origin
  [`agentculture/convertible`](https://github.com/agentculture/convertible),
  re-broadcast via guildmaster — guildmaster
  [#51](https://github.com/agentculture/guildmaster/pull/51)). Every agent
  cloned from this template now inherits the ability to hand a scoped task to a
  *different* engine/mind: `explore` (read-only investigation), `review` (a
  diverse second opinion on the committed diff), and `write` (delegate a small
  implementation). `explore`/`review` run isolated in a throwaway `git worktree`;
  `write` refuses a dirty tree. Fulfils
  [#8](https://github.com/agentculture/culture-rules/issues/8).
- **Ledger + CLAUDE.md:** record `outsource` in `docs/skill-sources.md`
  (origin = convertible, re-broadcast via guildmaster; vendored verbatim — it
  already carries `type: command`) and document its *optional* runtime
  dependency on the `convertible` CLI (the skill exits with an install hint if
  absent, so a clone that never uses it is unaffected).

### Changed

### Fixed

## [0.1.3] - 2026-05-31

### Changed

- Expanded the clone-and-rename instructions in `CLAUDE.md`: added `README.md` to
  the rename targets and a portable `git grep` discovery command so a cloner can
  find every occurrence of the template name (hard-coded in ~100 places across the
  package, including the CLI command files and `_ISSUES_URL` in
  `culture_rules/cli/__init__.py`) rather than renaming by hand.
- Synced `README.md`'s "Make it your own" checklist with `CLAUDE.md`: it now lists
  `README.md` itself as a rename target and points to `CLAUDE.md`'s discovery
  command as the authoritative procedure, so the two onboarding checklists no
  longer drift.

## [0.1.2] - 2026-05-30

### Changed

- Renamed the PR-lifecycle CLI references `agex` / `agex-cli` to `devex` (same
  tool, new name) across `CLAUDE.md`, `docs/skill-sources.md`, `.gitignore`, and
  the vendored `cicd`, `assign-to-workforce`, and `communicate` skills — the
  `cicd` scripts now invoke `devex pr`.
- Logged the vendored-skill in-place patch as a local divergence in
  `docs/skill-sources.md`; the matching canonical rename is tracked upstream for
  guildmaster in
  [agentculture/guildmaster#48](https://github.com/agentculture/guildmaster/issues/48)
  so a future re-sync reconciles cleanly.
- Aligned the documented `devex` version floor to `>=0.21` across the vendored
  `cicd` `SKILL.md` and `workflow.sh` install hint (were `>=0.1`), matching
  `docs/skill-sources.md` and the `await`-era feature set; flagged upstream on
  guildmaster#48.

### Fixed

- SonarCloud now reports code coverage — added `relative_files = true` to
  `[tool.coverage.run]` so `coverage.xml` emits repo-relative paths that map to
  `sonar.sources=culture_rules` (absolute / `.venv` paths were dropped
  as unmappable). Mirrors the sibling `convertible` setup.

## [0.1.1] - 2026-05-26

### Changed

- **CI gates on the SonarCloud quality gate**
  ([issue #3](https://github.com/agentculture/culture-rules/issues/3)) —
  added `sonar.qualitygate.wait=true` to `sonar-project.properties` so a failing
  gate fails the `test` job when `SONAR_TOKEN` is set. Token-less repos and fork
  PRs remain green (the scan step is guarded by `if: env.SONAR_TOKEN != ''`).

## [0.1.0] - 2026-05-26

### Added

- **Onboarded into the AgentCulture mesh** ([issue #1](https://github.com/agentculture/culture-rules/issues/1)).
- **Agent-first CLI** cited from teken's (`afi-cli`) `python-cli` reference
  (`teken cli cite`) — verbs `whoami`, `learn`, `explain`, `overview`, `doctor`,
  and the `cli` noun group. Runtime is self-contained (`dependencies = []`);
  `teken>=0.8` is a dev dependency only. Passes the seven-bundle agent-first
  rubric (`teken cli doctor . --strict`). `doctor` checks the agent-identity
  invariants (prompt-file-present, backend-consistency, skills-present).
- **Mesh identity**: `culture.yaml` (`suffix: culture-rules`,
  `backend: claude`) and the matching `CLAUDE.md` prompt file.
- **Canonical guildmaster skill kit** (11 skills) vendored under
  `.claude/skills/` (cite-don't-import): `agent-config`, `assign-to-workforce`,
  `cicd`, `communicate`, `doc-test-alignment`, `pypi-maintainer`, `run-tests`,
  `sonarclaude`, `spec-to-plan`, `think`, `version-bump`. Every `SKILL.md`
  carries `type: command` (load-bearing for the culture/claude backend);
  `cicd` / `communicate` consumer-identifying prose adapted, all script bodies
  verbatim. Provenance in `docs/skill-sources.md`. Three skills (`think`,
  `spec-to-plan`, `assign-to-workforce`) originate in `devague`, re-broadcast
  via guildmaster.
- **Build + deploy baseline**: `pyproject.toml` (hatchling), `tests/` (pytest,
  xdist, coverage), `.github/workflows/{tests,publish}.yml` (CI rubric/lint gate,
  PyPI Trusted Publishing), `.flake8`, `.markdownlint-cli2.yaml`,
  `sonar-project.properties`, and `.claude/skills.local.yaml.example`.

### Changed

### Fixed
