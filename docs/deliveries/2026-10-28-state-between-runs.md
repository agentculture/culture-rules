# Delivery Summary — State between runs

plan: `state-between-runs` · run: `partial` · date: `2026-10-28`
baseline: `devague summary skeleton`

## Intent

> Workflows can keep a named set of keys between runs: generic builtins read the set (dropping items already in it) and add keys to it after a run's work succeeds, each key expiring after a configured window; the first configured use is the daily newspaper not repeating articles

After: The daily newspaper does not link an article (by URL) that an earlier delivered issue linked within the configured window; on 2026-10-28, 11 of 13 links had already appeared on 10-26 or 10-27

The code half (t1-t5) is delivered on branch `rules/run-state` as version 0.37.0, with nothing newspaper-specific in code. The live half (t6) cannot start until this branch is merged, released and rolled out, so the run is partial by design: the newspaper's behaviour (c2, c15, c24) is not yet claimed.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — Generic keyset library: set-name and key rules, declarative normalisation, document ids, and the store read/add/list/clear helpers
- `t2` — Builtins keyset.filter and keyset.add (node ports over the keyset library)
- `t3` — Admin keysets verbs: API routes, CLI noun, MCP tools, OpenAPI, explain and learn
- `t4` — Wire the builtins: runner registry, describe phrases, editor builtin list, guide, version
- `t5` — Flow test: two-run keyset workflow, add before the commit step, and the boundaries
- `t6` — Live: roll out, seed the set, wire the daily newspaper, measure 7 issues

## Actual Delivery

All 6 plan tasks are accounted for.

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `culture_rules/keysets.py` (stdlib only): set-name and key checks, declarative `normalise`, sha256 document ids, insert-if-absent `add_keys`, `seen`, `list_sets`, `show_set`, `clear_key`, `clear_set`, `ensure_ttl_index`; `tests/test_keysets.py` (45). Merge `f8963ba` |
| `t2` | delivered | `culture_rules/node/actions/keyset.py`: `KeysetFilterPort` and `KeysetAddPort`; `tests/node/test_keyset_builtins.py`. Merge of `f3e8654`; later amended by d2 (`items_field`) and d4 (keyless handling) |
| `t3` | delivered | `keysets list`, `show` and `clear` as API routes, CLI verbs and MCP tools, admin-only, `clear` dry-run by default; `api/openapi.json` regenerated; explain and learn entries; tests (64). Merge of `7cc445c`; clear later audited (d1) and its applied count aligned with its dry run |
| `t4` | delivered | runner registration and docstring, `BUILTIN_WORDS` phrases, the editor's builtin list (web tests), `docs/operations/keysets.md`, the four harness prompt files, version 0.37.0. Commit `35b954d` |
| `t5` | delivered | `tests/rules/test_keyset_flow.py` (40): two-run pair, add before the commit step, a newspaper-shaped search loop through render, the boundary diff check. It found the loop-shape defect fixed by d2. Merge `e50f5fe` |
| `t6` | blocked | not started: it needs this branch merged, released and rolled out to every node and the API first (plan risk r1) |

## Mid-work Decisions

- `d1` (approved) — keysets clear --apply becomes an audited mutating verb (keysets.clear): its deletes run in one store transaction with exactly one audit entry (identity, set, key or whole set, count), and it joins the registry-wide audit test; t3 shipped it unaudited, like variable writes — a cleared set is destructive and hard to reverse (only a re-seed restores it) and, unlike a variable, leaves no version history of who cleared what; the decider (assess, 0.946 then 0.964 with the effort and trace facts removed) and the operator chose to audit it in this PR
- `d2` (proposed, for PR review) — keyset.filter and keyset.add gain an optional `items_field` setting for a loop's output objects; a None entry is an iteration with no items — t5's flow test showed a for_each loop's results port carries each iteration's last step output object, not a list of items, and without it the add stored 0 keys silently
- `d3` (proposed; recommended for rejection) — keyset.add fails whenever every given item is keyless — superseded by d4 when building it showed an ordinary filter-then-add day (only keyless survivors) would fail
- `d4` (proposed, for PR review) — keyset.add fails `bad_params` only when a keyless item holds a list-valued field (a loop output object), with a hint to set `items_field`; plain keyless items are skipped and counted in a new `keyless` output — re-assessed: fail_on_loop_shape 0.889, and a keyless-survivor day is ordinary (0.880)
- t3's dry-run and applied clear reported different counts (live keys against stored documents); the applied clear now reports the live count too and still deletes expired documents not yet reaped. No deviation: it makes c18's dry run match its apply.
- t1's task agent returned no report, so its tests were not shown failing first; the main agent ran four mutants instead (lapse `l1`, proposed).

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t3` (`d1`) | a cleared set is destructive and hard to reverse and leaves no version history; audited in this PR | `acceptable` |
| `t2` (`d2`, proposed) | a loop's results port carries output objects, not item lists; `items_field` added, no field name in code | `acceptable` |
| `t2` (`d3`, proposed) | failing every all-keyless add would fail ordinary filter-then-add days; superseded by d4 | `acceptable` |
| `t2` (`d4`, proposed) | refuse only the loop-output shape, count plain keyless items | `acceptable` |
| `t1` (`l1`, proposed) | tests not seen failing before the code; mutation check run instead | `acceptable` |
| `t6` | not started; needs the release on every node | `needs-follow-up` |

## Evidence

- tests: 468 passed at `4f861ad` across `tests/test_keysets.py` (45), `tests/node/test_keyset_builtins.py` (88), `tests/rules/test_keyset_flow.py` (40), `tests/server/test_keysets_api.py` (28), `tests/cli/test_keysets_verbs.py` (36), `tests/server/test_keysets_clear_count.py` (4), `tests/server/test_keysets_clear_audit.py` (8), `tests/node/test_keyset_wiring.py` (5), `tests/node/test_epub_bytes_pinned.py` (7), `tests/engine/test_audit_lifecycle.py`, `tests/test_surface_parity.py`, `tests/server/test_openapi_contract.py`, `tests/model/test_describe.py` (evidence e1-e13, proposed)
- full suite: 8510 passed, 8 skipped (an absent optional events dependency and one report-only cross-repo gap) at `4f861ad`
- web: 909 vitest and 128 Playwright passed; typecheck clean
- lint: black, isort, flake8, `teken cli doctor . --strict`, harness smoke (6 passed), `scripts/scan-secrets.py`, portability lint, markdownlint — clean
- failing first: t2 (import error before the module), t3 (56 of 64), t4 wiring (5 of 5; the editor tests 3 of 11 without the change), d1 (9), the clear count (2 of 4), t5 (10 of 39, 8 of them the real defect)
- deltas: b1 (`d1`, added), b2 (items_field, amended), b3 (keyless handling, amended), all proposed
- assessments: the decider's requests and answers for d1 and d3/d4 (job scratch files `assess-audit*.json`, `assess-keyless*.json`)
- commits: `origin/main..rules/run-state` (29 commits, from `50c3ae2` to the summary)

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| `c4` — keyset.add stores one document per key with its first-seen time, insert-if-absent | high | e1 · `tests/test_keysets.py`, `tests/node/test_keyset_builtins.py` |
| `c5` — keyset.filter drops in-window keys in order, keeps keyless items, outputs no stored key | high | e2 · `tests/node/test_keyset_builtins.py` |
| `c6` — keys expire by window and TTL index; max_keys refuses rather than truncates | medium | e3 · the TTL index itself is only exercised against MemoryStore; a live Mongo index is checked in t6 |
| `c12` — declarative normalisation with no built-in list | high | e4 · `tests/test_keysets.py` |
| `c20` — key cap and hashed ids | high | e5 · `tests/test_keysets.py` |
| `c23` — list, list-of-lists and (d2) loop output objects in, same shape out | medium | e6 · `tests/rules/test_keyset_flow.py`; d2 is still proposed |
| `c21` — counts out, never keys | medium | e7 · `tests/node/test_keyset_builtins.py`; the `keyless` output on add comes from d4, still proposed |
| `c18` — admin keysets verbs, dry run by default, audited clear | high | e8 · `tests/server/test_keysets_api.py`, `tests/cli/test_keysets_verbs.py`, `tests/server/test_keysets_clear_audit.py`; d1 approved |
| `c19` — one set name, one namespace | high | e9 · `tests/test_keysets.py` |
| `c7` — the add before the commit step marks only finished work, a re-run adds once | high | e10 · `tests/rules/test_keyset_flow.py` |
| `c1` — a keyset-only workflow drops on its second run what its first added | high | e11 · `tests/rules/test_keyset_flow.py` |
| `c13`, `c8`, `c10` — newspaper builtins, trust, validation and variables untouched | high | e12 · boundary test in `tests/rules/test_keyset_flow.py`; `tests/node/test_epub_bytes_pinned.py` |
| `c11` — registered and described | high | e13 · `tests/node/test_keyset_wiring.py`, `tests/model/test_describe.py` |
| `c3` — a flow author can wire the steps from the guide alone | low | `docs/operations/keysets.md` exists; nobody has wired a flow from it yet (t6 will) |
| `c2` — the live newspaper links no URL an issue of the last 14 days linked | unverified | t6 not started (o15) |
| `c15` — 0 repeats over the first 7 issues | unverified | t6 not started (o15) |
| `c24` — the go-live seed | unverified | t6 not started (o14) |
| `c14` — the 11-of-13 before-figure is reproducible | medium | measured once in the scope pass from the three stored runs; the t6 script will re-run it |

Lapse ledger evidence: `l1` (grader-unverified, t1's tests not seen failing first) is pending approval, not yet evidence. `c4`, `c6`, `c12`, `c19` and `c20` rest on t1's tests; the mutation check (four mutants, each failing 1-2 tests) is why they are not capped lower.

## Remaining Work / Follow-up

- `t6` — after this PR merges: release 0.37.0, roll out to every node and the API (r1), reproduce the before-figure, seed the newspaper's set from the last 14 daily runs (dry run first), wire `keyset.filter` into the search loop and `keyset.add` (with `items_field: results`) just before `record` with a raised search count, then count repeats over 7 issues (o14, o15). Operator-gated, every live write approved.
- Decide d2-d4 at the PR: approve d2 and d4, reject d3 (recommended).
- Decide lapse `l1`.
- r2 — whether a raised search count leaves enough fresh results per section is unmeasured (c16); the first live week's No results count will show it.
- The editor does not offer `items_field` (or any keyset setting) as a form field; it is set in the step config through the CLI or JSON, as the other builtins' settings are.
