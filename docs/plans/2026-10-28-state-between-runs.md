# Build Plan — State between runs

slug: `state-between-runs` · status: `exported` · from frame: `state-between-runs`

> Workflows can keep a named set of keys between runs: generic builtins read the set (dropping items already in it) and add keys to it after a run's work succeeds, each key expiring after a configured window; the first configured use is the daily newspaper not repeating articles

## Tasks

### t1 — Generic keyset library: set-name and key rules, declarative normalisation, document ids, and the store read/add/list/clear helpers

- instruction: Model the store pattern on `culture_rules`/node/actions/cursor.py (`update_if`, upsert when the expected value is None) and the TTL pattern on `culture_rules`/actors/handoff.py:321 (getattr `ensure_index`). Collection name keysets. No newspaper, URL-parameter or window constant anywhere; every rule comes from the config dict passed in. Keep the module importable with zero third-party dependencies.
- covers: c12, h13, c19, h18, c20, h19, c6, h7
- acceptance:
  - `culture_rules`/keysets.py (stdlib only) validates a set name against letters, digits, `_` : - up to 100 characters and raises a `bad_params`-style error otherwise
  - normalise(value, config): with no config returns the value unchanged; with `lower_host`, `drop_fragment`, `drop_trailing_slash` and `drop_query` patterns (fnmatch, e.g. `utm_`\*, ref) <http://Example.com/a/?utm_source=x#f> and <https://example.com/a> give one key; `drop_scheme` is a config switch too; no pattern list is built in
  - a normalised key over 2048 characters is refused; `doc_id`(set, key) is the sha256 hex of set NUL key; two keys equal for 200 characters and different after give different ids
  - store helpers over StoreOps: `add_keys`(store, set, keys, now, `window_s`) inserts one document {id, set, key, `first_seen`, `expires_at`} per key only if absent (`update_if` with upsert, expected none) and returns added and `already_present` counts; seen(store, set, keys, now) returns the keys with `expires_at` after now; `list_sets`, `show_set`, `clear_key`, `clear_set`; `ensure_ttl_index`(store) calls store.`ensure_index` on `expires_at` with `ttl_seconds` 0 when the store has it (getattr, as handoff.py does)
  - tests/`test_keysets.py` (MemoryStore): retried and repeated adds leave one document and keep `first_seen`; a key whose `expires_at` has passed is not returned by seen even though its document exists; two sets with one name share keys

### t2 — Builtins keyset.filter and keyset.add (node ports over the keyset library)

- instruction: Follow the port shape of `culture_rules`/node/actions/cursor.py (CursorAdvancePort): read config and inputs, validate, call `culture_rules`/keysets.py, return outputs. Do not register the ports in runner.py here (t4 does it) so this task only adds new files. Call `ensure_ttl_index` on first use.
- depends on: t1
- covers: c4, h4, h5, c5, h6, h8, c21, h20, c23, h21
- acceptance:
  - `culture_rules`/node/actions/keyset.py defines KeysetFilterPort and KeysetAddPort; config (or inputs) set, `key_field`, normalise, `window_days`; add also `max_keys`; a missing or bad value fails `bad_params`
  - keyset.filter: input items (a list, or a list of lists one level deep) returns items in the same shape and order without those whose normalised key was seen in the window; items with no or an empty key field are kept; outputs items plus kept, dropped and keyless counts and never a stored key
  - keyset.add: input items (same shapes) adds every item's normalised key with `expires_at` = now + `window_days`; more than `max_keys` keys fails `bad_params` and writes nothing; outputs added and `already_present` counts; a retried step adds nothing twice
  - tests/node/`test_keyset_builtins.py` covers each criterion above with MemoryStore, plus two concurrent adds of one key leaving one document, and a grep test that `culture_rules`/keysets.py and node/actions/keyset.py name no newspaper, utm or url constant

### t3 — Admin keysets verbs: API routes, CLI noun, MCP tools, OpenAPI, explain and learn

- instruction: Mirror the variables noun: `culture_rules`/server/app.py `_register_variables` and service.py Variables for the routes, `culture_rules`/cli/`_commands`/variables.py for the verbs, `culture_rules`/mcp/tools.py, and the REGISTRY in cli/verbs.py. Read and clear only through `culture_rules`/keysets.py helpers. Regenerate api/openapi.json with the project's export script. Do not touch node/runner.py or describe.py (t4).
- depends on: t1
- covers: c18, h17
- acceptance:
  - GET /keysets (names with key counts), GET /keysets/{name} (keys with `first_seen` and `expires_at`), POST /keysets/{name}/clear with an optional key and `dry_run`; all admin-only in auth/policy.py, 403 for viewer and editor tokens
  - culture-rules keysets list|show|clear with --json; clear is a dry run unless --apply; MCP tools `keysets_list`, `keysets_show`, `keysets_clear`; explain catalog entries and a learn mention; tests/`test_surface_parity.py` and tests/server/`test_openapi_contract.py` pass with api/openapi.json regenerated
  - tests for the routes and verbs: a non-admin gets 403 on all three; clear without --apply leaves the documents; clear --apply with a key removes one, without a key removes the set

### t4 — Wire the builtins: runner registry, describe phrases, editor builtin list, guide, version

- instruction: Small wiring only. Find the editor's builtin list where document.sections was added in 0.36.0 (git show b568def -- web/ shows it). The guide's example may use the newspaper shape but must say it is configured live, not shipped.
- depends on: t2, t3
- covers: c11, h12, c3, h3
- acceptance:
  - node/runner.py registers keyset.filter and keyset.add and lists them in its docstring; model/describe.py `BUILTIN_WORDS` has a phrase for each; tests/model/`test_describe.py` and the describe fixtures pass
  - the editor's builtin list offers both (web typecheck and vitest pass); docs/operations/keysets.md explains config, list shapes, the add-before-commit placement and the admin verbs, with a worked example that is not applied; CLAUDE.md and the three harness prompt files mention the capability; version bumped minor with a CHANGELOG entry

### t5 — Flow test: two-run keyset workflow, add before the commit step, and the boundaries

- instruction: Model on tests/rules/`test_sources_manifest_flow.py` (engine plus MemoryStore, a fake step failing on demand). The boundary diff check may run in the test suite only if it skips cleanly outside a git checkout; otherwise run it as a scripted check recorded as evidence.
- depends on: t4
- covers: c1, h1, c7, h9, c8, h10, c10, h11, c13, h14
- acceptance:
  - tests/rules/`test_keyset_flow.py`: a workflow built only from keyset.filter and keyset.add over a fixed list drops on its second run the items its first run added
  - a run that fails before the add step leaves the set unchanged; a run that fails after the add and before its commit step, then re-runs, adds each key once
  - a newspaper-shaped fixture (a `for_each` search loop yielding one list per section, then keyset.filter, newspaper.filter, render) shows a seen URL gone from the rendered html and an emptied section as No results.
  - a test asserts git diff origin/main --name-only touches none of newspaper.py, `newspaper_render.py`, `newspaper_issue.py`, actors/trusted.py, model/validate.py, model/`action_kinds.py`, and no node module calls `put_variable`; tests/node/`test_epub_bytes_pinned.py` passes

### t6 — Live: roll out, seed the set, wire the daily newspaper, measure 7 issues

- instruction: Operator-gated like the #97 t6: a rollout script in a worktree, every live write approved, the admin token only via grant run --inject. Nothing newspaper-specific is committed; the bundle may gain a disabled template.
- depends on: t5
- covers: c2, h2, c14, h15, c15, h16, c24, h22
- acceptance:
  - every node and the API run the new version (rollout verify ok) before any live workflow names keyset.\*
  - the 11-of-13 before-figure is reproduced by one script from runs run-498cad51, run-90056447, run-ada99021 (h15); the same script, with the live normalisation, later counts repeats
  - a one-off seed workflow, configured live and dry-run first, uses run.read over the last 14 daily runs and keyset.add; afterwards keyset.filter over the 10-26..10-28 search results drops all their links (h22); the workflow is deleted after
  - daily-newspaper gains keyset.filter in the search loop (between `search_web` and filter) and keyset.add just before record, with a raised search count, through the CLI with operator approval per batch; describe shows the new steps
  - over the first 7 issues: 0 repeated links within 14 days, and the No results sections counted and reported (c15, o15)

## Risks

- [follow_up] Every node and the API must run the new version before the live workflow names keyset.\* (an older node fails the step as an unknown builtin) (task t6)
- [unknown_nonblocking] c16 is unmeasured: a raised search count may still leave sections empty; measured in t6's first week by the No results count (task t6)
