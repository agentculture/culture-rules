# Build Plan — jira-devague-spec

slug: `jira-devague-spec` · status: `exported` · from frame: `jira-devague-spec`

> Opening a Jira issue starts a devague conversation in its comments: the engine asks scope, think and challenge questions, I answer in Jira, and a Fable 5.2 agent writes the converged devague spec, which lands in the library for my review.

## Tasks

### t1 — ADF helpers: flatten ADF to plain text, and plain multi-line text to ADF paragraphs/lists (extensions/jira/adf.py, new)

- instruction: Pure functions, no I/O. Keep today's one-line ADF byte-identical (o2). Put the module next to client.py; do not touch client.py in this task.
- covers: h3
- acceptance:
  - tests/extensions/`test_jira_adf.py`: flatten() turns paragraphs, hardBreaks, bullet/ordered lists, mentions and code blocks into plain text with newlines; unknown node types keep their text children; a non-ADF string passes through unchanged
  - `to_adf`('a') equals today's single-paragraph document exactly; `to_adf` of N lines gives N paragraphs; consecutive '1. '/'- ' lines become one orderedList/bulletList
  - stdlib only; no import outside `culture_rules`.extensions.jira

### t2 — JiraClient reads the full issue: description, every comment (paginated) with author accountId and created, and named custom fields; `add_comment` sends `to_adf`(body) (extensions/jira/client.py)

- instruction: Use /rest/api/3/issue/{key}?fields=... and /rest/api/3/issue/{key}/comment?startAt=&maxResults=; flatten bodies with adf.flatten. Keep `get_issue` as-is for the webhook.
- depends on: t1
- covers: c4
- acceptance:
  - tests/extensions/`test_jira_client_read.py` with a fake transport: `get_issue_full`(key, fields=\[...\]) returns {key, project, summary, description (flattened), reporter, comments:\[{id, author, created, body}\], fields:{name: value}}; comments over several pages are all returned in order; no 500-char cut
  - `add_comment` of a one-line body sends exactly the same JSON as before (regression test); a multi-line body sends `to_adf`'s document
  - existing tests/node/`test_jira_action.py` and tests/server/`test_hooks_jira.py` pass unchanged; the token never appears in repr or errors

### t3 — Jira webhook puts the author on every event: data.author (accountId) on jira.issue.created/updated and jira.comment.created (extensions/jira/hooks.py)

- instruction: Only `_key_data`/`_process` gain the author; do not change auth, dedup or the event type mapping.
- covers: c33
- acceptance:
  - tests/server/`test_hooks_jira_author.py`: an `issue_created` delivery stores data.author = payload user accountId; a `comment_created` delivery stores the comment author's accountId; `self_authored` is unchanged; all other data fields unchanged
  - a delivery with no author stores data.author = null, and nothing else changes

### t4 — jira.read action kind: register it and its port (extensions/jira/`__init__.py`, extensions/jira/read.py new)

- instruction: Mirror port.py's `_connection` and project check; reuse JiraClient.`get_issue_full`. Read-only: never call `add_comment`.
- depends on: t2
- covers: c3, h2, c20
- acceptance:
  - tests/node/`test_jira_read_action.py`: params actor, issue (required), fields (optional list of custom field names); output {key, project, summary, description, reporter, comments, fields}; an issue outside connection.projects fails `project_not_allowed` before the secret resolver is called; a non-jira or disabled actor fails `actor_not_found`
  - GET /actions/kinds lists jira.read with its typed params; web/src/fixtures/action-kinds.data.ts regenerated with scripts/export-action-kinds.py --write; describe has a phrase for it
  - `supports_idempotency_key` is True (a read is repeatable); `declarable_by_apps`=True

### t5 — jira.\* trigger filter users: match data.author against a list, never the actor's `self_identity` (extensions/jira/filters.py new; register in extensions/jira/`__init__.py`)

- instruction: Follow extensions/discord/filters.py and ext.`trigger_filter`(namespace='jira', match=, validate=, describe=).
- depends on: t3, t4
- covers: c7, h5, h27, h15
- acceptance:
  - tests/extensions/`test_jira_filters.py`: a jira.comment.created or jira.issue.created trigger with params.users \[A\] fires for author A only; an event with `self_authored` true never fires even if its author is listed; no users param keeps today's behaviour
  - validate refuses users that is not a non-empty list of strings; describe says the filter in words (e.g. 'by A or B')
  - tests/`test_extension_boundary.py` stays green

### t6 — Runner argument type segment: one safe path segment for registered commands (`culture_rules`/actors/code.py)

- instruction: Add one entry to the type table near code.py:128; the refusal is a bind error like other type mismatches.
- covers: c9, h21
- acceptance:
  - tests/actors/`test_code_segment_param.py`: a param declared 'segment' accepts 'SCRUM-12' and refuses '', '.', '..', any value with '/', '\\' or NUL, and values over 128 chars, before any process starts; existing types unchanged
  - docs for machine.command list the new type

### t7 — Bridge step forwards an optional `working_path` and accepts an operating sandbox; read-only locks still hold (`culture_rules`/actors/agent.py)

- instruction: Extend `BRIDGE_CORE_INPUTS`/`PASSTHROUGH_CONFIG` minimally; the sandbox name must match the cultureagent role from t8.
- covers: c13, h10
- acceptance:
  - tests/actors/`test_bridge_working_path.py`: a step input `working_path` (absolute path string) is forwarded in the bridge input; a relative path is refused; an actor pinned read-only refuses sandbox 'operate' (`sandbox_locked`); an actor allowing 'operate' forwards it
  - the agent's completed output reaches later steps unchanged (existing callback tests pass)

### t8 — cultureagent: Claude bridge operating role (separate repo, its own PR)

- instruction: Work in a ../cultureagent worktree. Reuse backend.py's read-only baseline measurement and config.py's tool-list pattern; the role name 'operate' is the contract with t7.
- covers: c30, h24, h30, h23
- acceptance:
  - in ../cultureagent: sandbox 'operate' runs claude -p with Bash limited to configured command patterns and one per-invocation writable `working_path`; the reviewer's checkout baseline check fails the run on any checkout change; writes outside `working_path` are refused; git push always denied; --model passes through
  - cultureagent tests cover each refusal; its version bumped and PR opened via its own cicd

### t9 — Docs: Jira actor guide gains jira.read, data.author and the users filter; a generic guide for comment-driven conversations with an agent (docs/actors/jira.md, docs/operations/jira-conversations.md new)

- instruction: Docs describe what is on disk; anything ahead of reality is marked planned.
- depends on: t4, t5, t6, t7
- covers: c5, h4, c37, h29, c12, h9
- acceptance:
  - the guide describes the generic shape only: rule on jira.issue.created, rule on jira.comment.created with `concurrency_key` jiraconv:{trigger.data.key}, users filter, `max_attempts`, `on_failure` posting run.error.code, jira.read -> bridge step -> registered command -> jira.comment; devague appears at most as an example under docs/
  - markdownlint-cli2 passes on both files

### t10 — Boundary test: no devague literal in culture-rules code, tests or editor (tests/`test_extension_boundary.py`)

- instruction: Build the needle so the test file itself does not contain the literal.
- covers: c28, h20, c8, h6
- acceptance:
  - a new test fails if 'devague' (case-insensitive) appears in `culture_rules`/, tests/ (except this test's own needle) or web/src
  - the test passes on the branch

### t11 — Release: version bump and CHANGELOG for the generic Jira/runner/bridge changes (pyproject.toml, CHANGELOG.md, uv.lock)

- instruction: Only this task touches pyproject.toml, CHANGELOG.md and uv.lock.
- depends on: t4, t5, t6, t7, t9, t10
- acceptance:
  - /version-bump minor; CHANGELOG lists jira.read, data.author, the users filter, the segment type and `working_path`; full suite, lint, scan-secrets and teken doctor green

### t12 — Core wrapper for devague (outside the repo, on core): proposals-only for the agent, adjudication only from the reply step, and a post-run check

- instruction: Lives in ~/.local/bin (or a sibling ops repo), never in culture-rules. Registered on core's runner actor as machine.command entries with typed args (key: segment).
- covers: c34, h28
- acceptance:
  - a wrapper on core's PATH takes the issue key (segment) and a JSON move list; agent-mode calls force --origin llm and refuse confirm/reject/resolve/oblige --confirm; reply-mode calls accept them only with a cited allowlisted comment id; the frame dir is `<root>/<key>`
  - a post-run check diffs frame state before/after an agent run and fails (nonzero) if any record became confirmed/approved; tested with a scratch frame

### t13 — Live actors on core: library actor gains kind spec + md and a key minted --kind spec; Fable 5.2 bridge agent actor (operate, `working_path` root); runner commands for the wrapper and git ls-remote head lookup; jira-app answerer accountIds noted

- instruction: Configured live with --apply after a dry run; no secret literal anywhere (grant references only). Roll actors out before code that needs them is enabled.
- depends on: t8, t11, t12
- covers: c14, h11, h22
- acceptance:
  - culture-rules actors show for each actor lists the new params; a dry-run library.push of a scratch md as kind spec private succeeds against library.culture.dev, then the item is deleted
  - the bridge's repo allowlist admits only the operator's repos; a head lookup for one returns a 40-hex sha

### t14 — Live rules and workflows on core: round-one on jira.issue.created, reply rounds on jira.comment.created, users filter, `concurrency_key`, `max_attempts`, `on_failure`, final library.push; all shipped disabled until the trial

- instruction: Nothing committed: create with the CLI (dry run, then --apply).
- depends on: t13
- covers: c1, c23
- acceptance:
  - culture-rules workflows describe for both workflows reads as the generic shape; rules validate; both disabled
  - the agent's round instruction is step configuration; no brief added to the repo

### t15 — Trial on one real SCRUM issue: enable, run a full conversation, record the result

- instruction: Before enabling, confirm no existing bundle drives this flow (docs/rules) and probe v5 (Jira's double event on comments).
- depends on: t14
- covers: c25, h17, c27, h19, c26, h26, h1, c24, h16
- acceptance:
  - one SCRUM issue naming a repo: >= 2 question rounds; 0 culture-rules comments taken as answers; a comment by an unlisted account starts no round; exactly 1 library item, kind spec, private, bytes equal to the export
  - issue key, run ids and library item id recorded as devague evidence for the obligations (validate-delivery)

### t16 — Editor: runner argument type segment in the Actors command editor, and a Users field for jira.\* triggers in the trigger picker (web/src/actors/app-config.ts, web/src/rules/TriggerPicker.tsx)

- instruction: Follow how the discord Users field is built in TriggerPicker.tsx; keep the change small and add vitest cases next to the existing ones.
- depends on: t5, t6
- covers: c7, c9
- acceptance:
  - the Actors tab's command editor offers 'segment' as a param type; a stored segment param loads and saves unchanged
  - a jira.\* trigger shows a Users field (accountIds) writing params.users; discord.\* keeps its own fields; other triggers show none
  - npx vitest run and npm run typecheck pass; the Playwright suite and the webglass gate pass

### t17 — jira.comment treats an absent or empty connection.projects as every project (`culture_rules`/extensions/jira/port.py)

- instruction: Match jira.read's check (read.py): if allowed and project not in allowed.
- depends on: t4
- covers: c3
- acceptance:
  - tests/node/`test_jira_comment_projects.py`: with projects absent or \[\] a comment on any issue is sent; with projects \['SCRUM'\] an OTHER-1 issue fails `project_not_allowed` before the secret is read
  - existing tests/node/`test_jira_action.py` passes, updated only where it pinned the old empty-means-none behaviour (reported)

### t18 — jira.comment refuses a malformed issue key with `bad_input` (like jira.read); one shared project-allowlist normaliser used by the webhook, jira.comment and jira.read (`culture_rules`/extensions/jira/projects.py new, port.py, read.py, hooks.py)

- instruction: Extract one helper (e.g. `allowed_projects`(connection) -> frozenset\[str\] and `project_allowed`(key, allowed) -> bool) and use it in all three; also share `_connection` between port.py and read.py if it stays a small change.
- depends on: t17
- covers: c3
- acceptance:
  - tests/extensions/`test_jira_projects.py`: the shared helper uppercases both sides; absent or empty allows all; a lowercase projects list admits SCRUM-1 in the webhook, jira.read and jira.comment alike
  - jira.comment: a non-string or prefix-less issue fails `bad_input` before the secret is read (tests updated where they pinned `project_not_allowed`, reported); an allowlist miss stays `project_not_allowed`
  - full suite, lint and bandit pass; docs/actors/jira.md and CHANGELOG.md say `bad_input` for a malformed key

## Risks

- [unknown_nonblocking] Ops on core unverified (frame v3): whether a Claude bridge runs on core, its claude login there, and the Fable 5.2 model id for claude -p --model; t13 cannot finish until known. (task t13)
- [unknown_nonblocking] Jira may also send jira:`issue_updated` for each comment (frame v5, lapse l1); the reply rule must listen only to jira.comment.created; probe before enabling. (task t15)
- [unknown_nonblocking] The sandbox name 'operate' is a cross-repo contract between t7 (culture-rules) and t8 (cultureagent); a rename on either side breaks the bridge step. (task t7)
- [unknown_nonblocking] The main working tree has uncommitted, unrelated changes (discord `code_use`, github merge); build in `../.worktrees.culture-rules/<name>/` branches off main, never on top of them.
- [unknown_nonblocking] Jira comment pagination (startAt/maxResults on /issue/{key}/comment) and the comment size limit are from memory (frame v6); t2's fake-transport tests encode the assumption and need one live check. (task t2)
- [follow_up] Per-issue directories on core have no retention or cleanup when an issue closes (frame v4).
- [follow_up] Opening a PR from an agent's local commits through culture-rules (frame v2) is a later flow.
- [unknown_nonblocking] An operating agent's allowed Bash commands can still write outside `working_path` through shell redirects or the command's own file writes; Edit rules don't cover Bash and the checkout baseline only checks the repo. Mitigation: allow only the core wrapper (t12), which writes only under its own root. (task t12)
