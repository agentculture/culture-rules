# Build Plan — Daily newspaper

slug: `daily-newspaper` · status: `exported` · from frame: `daily-newspaper`

> Every morning at 06:30 Asia/Jerusalem, culture-rules searches the web for the operator's chosen sections, has Qwen summarise what it found, and delivers one dated newspaper by email (from a culture.dev sender) and as an EPUB queued for the operator's Xteink reader

## Tasks

### t1 — Model layer: the email and search app surfaces and the four action kinds

- instruction: Only model/validation files: `culture_rules`/model/`app_actor.py`, model/`action_kinds.py`, model/describe.py and their tests. No ports, no runner entries, no web files.
- covers: h14, h19, c26
- acceptance:
  - model/`app_actor.py` accepts surface email (connection: `api_key` grant ref, domain in {culture.dev, jetson-ai-lab.org}, `local_part` default no-reply, recipients list) and search (`api_key` grant ref); a domain outside the enum, a literal key or a malformed recipient is refused with a field path, through the API too
  - model/`action_kinds.py` catalogues email.send, search.web, llm.complete and library.push with typed params and a required actor; describe.py `ACTION_WORDS` covers them; `test_action_kinds` and `test_describe` updated

### t2 — email.send: a Resend client and port

- instruction: Model on apps/jira.py and node/actions/jira.py. New files only, plus their tests under tests/apps and tests/node. Do not edit runner.py.
- depends on: t1
- covers: c9, h1, h2
- acceptance:
  - apps/resend.py (URL constant, injectable transport, token hidden from repr) and node/actions/email.py; the from address is `local_part`@domain from the actor; a recipient outside the allowlist is refused before the key is resolved, naming no secret; the engine idempotency key is sent as Idempotency-Key and the port declares `supports_idempotency_key`; outputs {`message_id`}

### t3 — search.web: a Brave client and port

- instruction: New files only, plus tests. Do not edit runner.py.
- depends on: t1
- covers: c10, h3
- acceptance:
  - apps/brave.py and node/actions/search.py send query, count, freshness, country and lang with X-Subscription-Token from grant, call only api.search.brave.com, and return results as a list of {title, url, snippet, age, source} capped in count and per-field length

### t4 — llm.complete: an OpenAI-compatible completion port

- instruction: Stdlib urllib only. The locked brief is a constant in code (summarise only the given results, one bullet per item with its link, say 'no results' when empty). New files plus tests; do not edit runner.py.
- depends on: t1
- covers: c11, h4, c20
- acceptance:
  - node/actions/llm.py posts /v1/chat/completions to the actor's `base_url` with its model, the locked newspaper brief as the system message and the step's content as the user message, never a tools field, `max_tokens` capped; returns {text, model, usage}; an unreachable server or timeout is a distinct retryable failure code; the actor honours `concurrency_pool` and `max_concurrency`

### t5 — library.push: upload the EPUB to xteink and queue it

- instruction: Stdlib multipart encoder. New files plus tests; do not edit runner.py.
- depends on: t1
- covers: c14, h12
- acceptance:
  - node/actions/library.py sends a multipart POST /api/library (file, title, author culture-rules, kind article) with Bearer from the actor's grant ref, then `POST /api/devices/<id>/queue` with the returned item id; a refused upload queues nothing; outputs {`item_id`, created, `queue_id`}

### t6 — Newspaper builtins: sections, render, the per-date guard

- instruction: Stdlib only (zipfile, html, json). New files plus tests; do not edit runner.py.
- depends on: t1
- covers: c12, h11, c13, h7, c15, h8, c19, h5, h6
- acceptance:
  - node/actions/newspaper.py provides newspaper.sections (validates the JSON variable and fails naming the field), newspaper.render (HTML plus EPUB with mimetype first and stored, container.xml, OPF, nav and NCX, one chapter per section, escaped fields, a size cap; a section with no results says so; a missing summary renders the results with the unavailable note) and newspaper.issue (the local date from the slot and tz; claim and record per date in a `newspaper_issues` collection)
  - a link check: every link in a summary must be one of that section's result URLs, otherwise the summary is dropped for the results-only rendering

### t7 — Editor: email and search surfaces, the domain drop-down, the four action forms

- instruction: web/ only.
- depends on: t1
- covers: c17, h14
- acceptance:
  - web/src/actors/app-config.ts gains the surfaces, their connection fields and an enum field type rendered as a select with exactly culture.dev and jetson-ai-lab.org; ActionPicker.tsx has param forms for email.send, search.web, llm.complete and library.push; types.ts kinds updated; vitest covers each; typecheck passes

### t8 — Wiring, the bundle and the end-to-end test

- instruction: Touches runner.py and docs/rules/newspaper, tests/rules/`test_newspaper`\*.py, docs/operations/newspaper.md.
- depends on: t2, t3, t4, t5, t6
- covers: c16, h13, h10, c1, h15, h17, h18, c18, h9, c27, h20, c25, c21, h16
- acceptance:
  - runner.py registers the four ports and three builtins; docs/rules/newspaper ships actors (email, search, llm with pool qwen-spark2, library; recipient allowlist empty), the workflow (sections, `for_each` search and summarise, render, guard, email, push) and the rule (schedule 30 6 \* \* \*, tz Asia/Jerusalem, disabled) plus variables.json and a seed script; it imports cleanly; scan-secrets passes
  - a behavioural test drives the workflow on fakes from a 06:30 Asia/Jerusalem slot (also across DST) to one email and one queued item, a re-run for the same date sends nothing, cortex down still sends the results-only issue, and the PR fixer bundle tests are unchanged
  - docs/operations/newspaper.md covers the operator's setup: grant names, the `xtk_` key, the variables, seeding and enabling

### t9 — Release: version, CHANGELOG, actor docs, the four prompt files

- instruction: Docs and version files only.
- depends on: t7, t8
- covers: c21
- acceptance:
  - minor version bump with a CHANGELOG entry; docs/actors/email.md, search.md, llm.md, library.md and the README table; CLAUDE.md, QWEN.md, AGENTS.override.md and AGENTS.colleague.md describe the new action kinds in step; teken doctor strict and harness-smoke pass

### t10 — Live: the operator seeds the variables and actors, and the first issue arrives

- instruction: Operator-run setup and rollout; Claude watches the first run and files the evidence.
- depends on: t9
- covers: c23
- acceptance:
  - After the operator's setup and rollout, the 06:30 Asia/Jerusalem run delivers the issue to the inbox and queues it on the device; its run shows the message id and item id
