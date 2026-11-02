# No hardcoded prompts

> llm.complete and the render builtins hold no prompt or content-shape rule: briefs, answer shapes, caching, list numbering, reference replacement and output validation are generic steps and options, configured per workflow

## Audience

- The operator, who configures every prompt, answer shape, citation template and validation rule as actor and step configuration, and every existing workflow that must keep running through the change

## Before → After

- Before: Built-in briefs, a fixed answer validator, an answer cache tied to one brief name, and a hardcoded bullet-and-link check in a render builtin are code; model output that cites by number is refused because its lines are not bullets
- After: No prompt or shape rule is code: briefs are actor config, steps name their brief, the answer shape and cache are step options, and a workflow can number a list for a model, replace \[n\] references by a configured template, validate the result by configured rules and render it
  - instruction: grep `culture_rules` for the old brief constants and fixed answer fields returns nothing; the newspaper issue renders a cited summary

## Why it matters

- Operator rule: code implements framework only, and process and business logic are configuration; changing a prompt or format becomes an edit, not a release

## Requirements

- llm.complete's built-in briefs leave the code: every brief name resolves only from the bound actor's params.briefs (admin-only), a step must name its brief, and there is no default brief
  - honesty: llm.complete with no brief, or a brief the actor lacks, fails `invalid_request` before any request; no brief text remains in llm.py
    - instruction: unit tests on llm.complete; grep llm.py for the old texts
- Every committed bundle and test that uses llm.complete names its brief and carries it as actor config, so nothing that ran before stops running
  - honesty: every workflow and bundle test that passed before still passes, with briefs only from actor config
    - instruction: full test suite
- A generic `answer` step option checks a model reply's JSON shape from step config (field names; types such as text, lines, word, list of strings; item counts, character limits, patterns), replacing the fixed answer validator in code
  - honesty: a configured shape accepts the answers the old validator accepted and refuses the same bad ones, with the same codes
    - instruction: port the old validator's test cases to a configured shape
- The answer cache is a step option for any brief; its file name and label are step config, defaulting to today's values so existing cache files stay readable
  - honesty: a cache file written by the old code is read back with no new request
    - instruction: test with a file written by 0.41.0
- Render builtins do not enforce a content-shape rule: the hardcoded summary check (`_summary_ok`) leaves render, which shows any text it is given, and the rule becomes the generic validation step's configuration
  - honesty: render shows prose with links unchanged, and the validation step configured with the old rule refuses exactly what the old check refused
    - instruction: port the old check's test cases to the validation step
- A new generic step numbers a list into text for a model's input (\[1\] then the configured fields of each item, format in config); today no builtin or {{ }} template can loop over a list (refs.py `_text` renders a list as str(list))
  - honesty: the numbering step's output for a list and template is deterministic and numbers from 1 in list order
    - instruction: unit tests
- A new generic replace step rewrites each \[n\] reference in a text using the nth item of a given list and an output template from step config (for example \[{n}\]({url})); the step is a basic text operation with no format of its own, and a reference outside the list is reported, never invented
  - honesty: every \[n\] with 1 <= n <= len(list) is replaced by the template; an out-of-range \[n\] is listed in an output and left unchanged
    - instruction: unit tests
- The editor and describe phrases follow the new options: the action picker offers the bound actor's brief names (no built-in list, no default) and the answer and cache options; the new steps get editor catalogue entries and describe phrases
  - honesty: the action picker shows only the chosen actor's brief names and the new options, with no built-in list or default
    - instruction: vitest on ActionPicker and StepEditor
- Rollback stays possible: the order is seed the actors' briefs (old code ignores actor briefs named like code briefs, so seeding is safe on 0.41.0), roll out, then move live steps to the new options; rolling the code back to 0.41.0 first requires putting the live steps back (a step using the answer option would otherwise lose its parsing), documented in docs/operations/rollout.md
  - honesty: rollout.md states the order (briefs, code, live steps) and the rollback order (live steps back, then code), and both are dry-run before any apply
    - instruction: read docs/operations/rollout.md; dry-run each step
- The validation step outputs a verdict and its reasons alongside the text, so later steps branch on it (when) to show the text or a configured fallback; refused output is never silently dropped
  - honesty: a refused output reaches later steps as a verdict with reasons, and a configured fallback is shown instead of nothing
    - instruction: unit test of the validation step plus a when branch in a test workflow
- A generic json.parse step turns a JSON string (for example a shared variable) into a value and validates it against a schema from step config (required, types, min/max, enum, defaults, item limits, unknown keys refused), failing fast with the exact field path
  - honesty: a schema in config reproduces every refusal the coded sections check gives today, with the same field path in the error
    - instruction: port the coded check's test cases to json.parse with a schema
- A generic list.filter step keeps the items of a list that satisfy a condition over item (from `culture_rules`/model/condition.py, extended with contains and any/all over a term list), with optional dedupe by a field, a derived host from a url and a cap
  - honesty: list.filter with conditions in config keeps exactly the items the coded filter kept for the same inputs
    - instruction: port the coded filter's test cases
- Generic value helpers: join strings with a separator, add seconds to a timestamp, and convert a slot time to a local date in an IANA time zone (DST-correct)
  - honesty: the local-date helper matches the coded slot-to-date result across a DST change, and time-add and join are exact
    - instruction: unit tests including DST boundaries
- A generic document.render step renders a list of records into HTML and an EPUB (chapters) from a template, CSS, texts and caps in step config, choosing a variant per record by condition (for example text, else items with a fallback note), deterministic so identical input gives identical bytes; it reuses `_epub.py`
  - honesty: document.render with a template in config renders every case the coded renderer handled (no results, text, items with a fallback note), and identical input gives identical bytes
    - instruction: port the render test cases plus a golden test
- A generic named lease and send-once ledger replace the coded issue state machine: a key-addressed lease with owner, expiry, takeover and a fence (`claim_lost`), and a ledger that freezes the first payload, records the attempt before sending, gives a stable idempotency key, bounds retries by a window and marks the outcome uncertain past it; collection, key template, windows and payload fields are step config
  - honesty: each of the 12 guarantees in c27 has a test against the lease and ledger steps
    - instruction: one test per guarantee
- With the generic steps in place, the four newsletter builtins (newspaper.sections, newspaper.filter, newspaper.render, newspaper.issue) and their modules are removed from code, and the committed bundle is rewritten as configuration of generic steps
  - honesty: after R3 no newspaper.\* builtin remains registered, and the committed bundle runs end to end on generic steps only
    - instruction: grep runner.py; run the bundle flow test

## Honesty conditions

- the four parts ship together: briefs in config, answer and cache options, the numbering and replace steps, the validation step
  - instruction: diff review: every hardcoded item named in c2-c8 is gone from code or became a step option
- builtins that read a parsed answer are unchanged
  - instruction: git diff
- the operator can change each prompt, answer shape, citation template and summary rule with actors update / workflows update alone, no release
  - instruction: do each once on a test actor/workflow
- the before state is what main 45d2357 holds
  - instruction: grep main 45d2357
- on the delivered branch the hardcoded items are gone and a test workflow renders a cited output
  - instruction: grep and one live test-workflow run
- no prompt, format or shape rule needed a release to change after this work
  - instruction: review the delivered step and actor options
- the three counts in the signal are read from live runs, not estimated
  - instruction: read 3 issues' render outputs and the transcript and recording runs
- a crash or retry at every fence (before send, after send, before record) neither sends twice nor skips the send
  - instruction: fault-injection tests at each fence

## Success signals

- After rollout: 0 prompt texts in `culture_rules` (grep finds none of the old brief texts); one test workflow numbers a list, asks a model to cite by number, replaces references by a template and validates the result with 0 texts or rules in code; every workflow that ran before the rollout completes its next run
  - instruction: grep the package; read 3 issues' render outputs and the live runs of transcript-to-reader and the recording flow

## Scope / boundaries

- Existing builtins that read a parsed answer keep reading it unchanged; no other builtin is removed in this work
- The lease and ledger keep every guarantee the coded state machine gives today: keyed by the configured key not the run; takeover only after expiry; lease renewed at every side-effect fence and a lost lease fails non-retryably; first payload frozen and replayed byte-for-byte; stable idempotency key; attempt recorded before send; never resent past the window (uncertain instead); the outcome id recorded even after a lost lease; a later side effect skipped when already recorded; done only once the send is settled, else the lease is released; an empty recipient list skips the send but completes

## Assumptions

- Rollout order: actors receive their briefs (admin actors update) before code that needs them is enabled, since rollout import does not touch actors; live steps move to the new options only after the new code runs

## Scope exploration

- `s1` — `culture_rules/node/actions/llm.py`: `_brief`() at 342-355 falls back to a built-in brief when none is named and lets built-in names win over actor briefs; the built-in brief texts are fixed at 156-194
  - seeds: `c2`
- `s2` — `docs/rules bundles`: two committed bundles use llm.complete: one names no brief (relying on the built-in default), one names a built-in brief; the bundle actor carries no briefs
  - seeds: `c3`
- `s3` — `culture_rules/node/actions/recording_answer.py`: the only answer validator, switched on by one brief name (llm.py:758); no generic JSON-shape validator exists in the repo (model/schema.py and validate.py cover dataclass models only)
  - seeds: `c4`
- `s4` — `llm.py answer cache`: `_cache_path` 525-541 requires one specific brief name; the key is the content bytes plus that name; the file name is pinned by tests/node/`test_llm_complete.py`:1012
  - seeds: `c5`
- `s5` — `culture_rules/node/actions/newspaper_render.py`: `_body` 333-340 is the only caller of `_summary_ok`; prose with \[n\](url) links renders fine, but line 229 rejects any non-bullet line
  - seeds: `c6`
- `s6` — `culture_rules/model/refs.py and builtins`: templates substitute scalars only; markdown.render, json.read, select and document.sections do not number lists; llm.complete sends a list content as JSON
  - seeds: `c7`
- `s7` — `culture_rules/node/actions (new step)`: nothing turns \[n\] into links today; `summary_to_xhtml` then renders the links
  - seeds: `c8`
- `s8` — `rollout and import`: rollout import imports workflows and rules only; docs/operations/rollout.md:241-242 says live actor changes are `actors update` between import and enable
  - seeds: `c9`
- `s9` — `culture_rules/node/actions/transcript_render.py`: a render builtin duplicates the fence, think and word parsing but needs only two fields of the parsed answer
  - seeds: `c10`
- `s10` — `challenge pass / adjacent-systems lens: web/src/rules/ActionPicker.tsx, culture_rules/model/describe.py`: the action picker hard-lists the built-in briefs with a default (ActionPicker.tsx:38-42,164); describe.py:135 phrases llm.complete
  - seeds: `c17`
- `s11` — `challenge pass / reversibility lens: llm.py _brief, live recording flow`: giving actors their briefs is safe before rollout (on 0.41.0 built-in names win over actor briefs); rolling the code back after live steps move to the answer option would drop parsing
  - seeds: `c18`
- `s12` — `challenge pass / observability lens: newspaper_render.py _body 333-340`: render today chooses between the text and two fallback notes itself; once the check moves out, the refusal must reach later steps to keep a fallback visible
  - seeds: `c19`
- `s13` — `challenge pass / unstated-assumption lens: newspaper_render.py:96-99`: the frame covers prompts and the shape rule, but a render builtin still holds four user-facing note texts; routed to a question
- `s14` — `challenge pass / security lens: auth/guards.py check_actor_briefs`: prompts stay admin-only (`briefs_admin_only`); the new answer, citation and validation options are workflow step config editors may change, which carry no prompt text; clean pass
- `s15` — `challenge pass / migration lens: llm.py answer cache 525-587`: c5 covers the cache name and label defaults; checklist ids depend on unchanged answers, residual risk only if the configured shape normalises differently (h4 checks it)
- `s16` — `challenge pass / concurrency lens: the new numbering, replace and validation steps`: pure functions of their inputs, no shared state added; clean pass
- `s17` — `culture_rules/node/actions/newspaper.py:133-307 (sections)`: parse, length, type, range, default and hostname checks are coded with fixed field names and caps; no step parses a JSON string (json.read reads files under a root)
  - seeds: `c22`
- `s18` — `culture_rules/node/actions/newspaper.py:250-332 (filter)`: must/`must_not` substring terms over title and snippet, host allow/deny with subdomains, dedupe by url and a count cap are coded; conditions have no contains, no any/all and do not filter lists
  - seeds: `c23`
- `s19` — `culture_rules/node/actions/newspaper_issue.py:174-198, newspaper.py:237-247`: local date from slot and tz, `summaries_until` and the OR-join are coded inside two builtins
  - seeds: `c24`
- `s20` — `culture_rules/node/actions/newspaper_render.py, _epub.py`: fixed HTML structure, CSS, texts, field limits, EPUB id and the per-section fallback chain are coded; `_epub.py` helpers are generic but not exposed as a step
  - seeds: `c25`
- `s21` — `culture_rules/node/actions/newspaper_issue.py:132-570, culture_rules/engine/claims.py`: the coded phases (claim, `before_email`, `before_push`, record) hold the dedupe guarantees; engine claims are keyed by run and step, not by a caller key, and no named-lease step exists
  - seeds: `c26`
- `s22` — `newspaper_issue.py invariants`: 12 invariants listed by the scope read; a double-send window opens if lease and ledger are split across two independent documents
  - seeds: `c27`
- `s23` — `culture_rules/node/runner.py builtin registry`: four newspaper.\* builtins registered; the committed bundle's steps sections, claim, filter, render, `email_gate`, `push_gate` and record call them
  - seeds: `c28`

## Decisions

- Code holds only basic units and operations (number a list, replace references, validate a shape, render); every prompt, format, template and shape rule is step or actor configuration
- Render builtins take every user-facing text (empty, unavailable and refused notes) from step configuration; the code cannot tell which flows run on it
- A newsletter is a flow and is never named in code or spec; Discord and Markdown are generic surfaces and may be
- Three releases: R1 briefs, answer, cache, numbering, replace and validation; R2 json.parse, list.filter, value helpers, document.render; R3 lease and ledger, then the four builtins are removed; the live flow keeps running between releases
- document.render's output may differ from today's once, at the switch; a golden test pins it afterwards

## Open parks

- [unknown_nonblocking] Whether docs/rules bundles should carry the live briefs' text (committed prompts as example config) or only placeholders, given 'commit is code and logic, user content lives in the store'

## Resolved vagueness

- [follow_up] The newsletter has its own builtins in code (`culture_rules`/node/actions/newspaper.py, `newspaper_issue.py`, `newspaper_render.py`): flow-specific code under the operator's rule (a newsletter is a flow; Discord and Markdown are generic). This work only removes their hardcoded prompt and shape rules; replacing them with generic steps is a later frame — resolved: operator 2026-11-02: include the builtins in this frame
