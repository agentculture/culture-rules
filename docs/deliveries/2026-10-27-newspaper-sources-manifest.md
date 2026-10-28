# Delivery Summary — newspaper sources manifest

plan: `newspaper-sources-manifest` · run: `complete` · date: `2026-10-27`
baseline: `devague summary skeleton`

## Intent

> Every daily newspaper issue in culture-library carries a sources manifest (each article's title, the URLs it links and a verbatim excerpt), pushed right after the issue, and the issues already in the library have one too

After: Each new newspaper issue in culture-library has a sources manifest within the same run that pushed it, the earlier issues have one from the backfill (skips listed), and newspaper evals can be generated for every issue

The run built only generic capabilities in code (culture-rules#97, PR #115, 0.36.0). The newspaper's flows, which are the daily wiring and the one-off backfill, were configured live through the CLI and never committed (`docs/operations/sources-manifest.md` is a worked example, not applied).

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — http.call resolves a header `<word> grant:<NAME>` (generic)
- `t2` — http.call `save_to`: stream the response body to a file under a root the actor lists (generic)
- `t3` — Generic sections builtin: split an EPUB, HTML or Markdown document at a configured heading
- `t4` — Wire the three capabilities: runner, catalog, describe, editor, docs, version
- `t5` — Flow test: the manifest flow end to end against a fake library
- `t6` — Live: rollout, before-evidence, key check, dry run, live config, backfill, validation
- `t7` — `for_each` outcomes: each iteration's status, errors and collected outputs as a loop output (generic engine)

## Actual Delivery

All 7 plan tasks are accounted for.

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `secrets.split_word_ref`; `http._header_value` sends the word and the resolved value; the save-time check accepts the word form only under `headers` (PR #115) |
| `t2` | delivered | http.call `save_to` with the policy keys `save_roots` and `save_max_bytes`; the body streams to `<root>/<sha256>.<ext>`; HEAD and OPTIONS are refused; a symlink at the target name is refused after the read (`d1`) (PR #115) |
| `t3` | delivered | the builtin `document.sections` (`culture_rules/node/actions/sections.py`): EPUB in spine order, HTML and Markdown; `split_on`, `paragraphs` and `max_chars` are required (PR #115) |
| `t4` | delivered | runner registration, the describe phrase, the editor's builtin list, the action-kinds catalog, `docs/actors/http.md`, `docs/operations/sources-manifest.md`, version 0.36.0; not in the explain catalog (`d2`) (PR #115) |
| `t5` | delivered | `tests/rules/test_sources_manifest_flow.py`: the daily and backfill flows end to end against a fake library, including a failed PUT (PR #115) |
| `t6` | delivered | 0.36.0 rolled out to 4 nodes and the API. The library actor's http policy, the variable `library_base`, the workflow `newspaper-sources-backfill` (17 issues, 72 entries) and `daily-newspaper` revision 6 were configured live. The first scheduled run, on 2026-10-28, PUT item 98's manifest with a 200; culture-library's eval found unmatched = 0 |
| `t7` | delivered | the `for_each` outcomes port and `config.collect` (scalars only; reserved names refused) in `culture_rules/engine/runs.py` and `culture_rules/model/validate.py` (PR #115, added by `d4`) |

## Mid-work Decisions

- `d1` — t2's criterion 'an existing symlink at the target name fails `path_refused` before any request is sent' is met after the read instead: the target name (sha256 plus extension) is known only once the body is read, so the request is sent and the body streamed to a temp file in the root; then `path_refused` fires, nothing is written through the symlink, and the temp file is removed (tested, including that the symlink's destination is never created). The other three refusals (outside `save_roots`, a symlinked root, no `save_roots`) still happen before any request — the file is named by the sha256 of its body, which does not exist before the request
- `d2` — t4's criterion 'listed in the explain catalog' is not met: `culture_rules`/explain/catalog.py documents CLI verbs only and lists no builtin or action kind (markdown.`from_html` and http.call are absent too), and no verb was added. document.sections is documented instead where builtins live: the describe phrase (`BUILTIN_WORDS`), the editor's builtin list, the module docstring and docs/operations/sources-manifest.md; `save_to` is in the action-kinds catalog (`action_kinds.py`) and docs/actors/http.md — the plan assumed the explain catalog lists builtins; it lists CLI verbs only
- `d4` — The plan gains task t7 (operator's decision on d3): `for_each` loop results carry each iteration's status, errors and collected outputs (a generic engine change, opt-in through an outcomes output port and config.collect), so a backfill-shaped run's outputs list every id with its outcome — a `for_each` result held only the last succeeded body step's outputs, so a skipped iteration's error could not reach the run's outputs; the operator chose the engine change in this plan over a follow-up issue
- `d3` was rejected (recorded and not taken); `d4` is the operator's alternative.
- An early design put a `library.sources` action kind in code. The operator ruled that code holds generic capabilities only, and flows and business logic are configuration. The design became generic steps plus live config, and claim c22 was rejected.
- Code review on PR #115, finding 3: the operator kept `document.sections`' `content_root` working the way `json.read` does (any absolute root). Whether builtin reads should be confined to node roots is a question on #116.
- Lint fixes made under the SonarCloud gate (S3776, S5713, S5778, S5806, S6353, S6466, S8786) reshaped helpers in `runs.py`, `sections.py` and `http.py` without changing behaviour; the feature tests passed after each fix.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|------------------------|-----------------|
| `t2` (`d1`) | the file is named by the sha256 of its body, which does not exist before the request | `acceptable` |
| `t4` (`d2`) | the plan assumed the explain catalog lists builtins; it lists CLI verbs only | `acceptable` |
| `t7` (`d4`) | a `for_each` result held only the last succeeded body step's outputs, so a skipped iteration's error could not reach the run's outputs; the operator chose the engine change in this plan over a follow-up issue | `acceptable` |
| `t3` (`l1`) | its tests were not seen failing before the code existed (lapse l1, approved); a later mutation check showed they catch breakage | `acceptable` |
| `t6` / o11 | o11's clause "a re-run whose push was skipped still PUTs the recorded item" has no test and no live observation | `needs-follow-up` |

## Evidence

Every record below is approved by the operator (evidence e1–e18, deltas b1–b3).

- tests: 265 passed at `ac8ae21`, whose code is the same as `main` `b568def`, across `tests/node/test_http_grant_scheme.py`, `tests/actors/test_secrets_word_ref.py`, `tests/node/test_sections.py`, `tests/rules/test_sources_manifest_flow.py`, `tests/node/test_epub_bytes_pinned.py`, `tests/node/test_http_save_to.py` and `tests/engine/test_for_each_outcomes.py` (e13–e18)
- CI: PR #115's checks passed with SonarCloud at 0 open issues and 0 hotspots before the squash merge to `b568def`
- live, backfill: `newspaper-sources-backfill` runs run-f89ac97f (dry run, item 81), run-b3458413 (item 81) and run-a77767e1 (16 items), with read-back run-ec8e1a14; 17 items and 72 entries, `GET /sources` 200 for every one (e1, e2, e6–e9)
- live, config: `actors show newspaper-library`, `variables get library_base`, `rules show daily-newspaper` and `workflows describe daily-newspaper` (revision 6) (e4, e5)
- live, daily: scheduled run run-498cad514bfddb42d11a31ba21f312cf at 05:00 IST on 2026-10-28: `sources` found 4 of 4, `put_sources` answered 200 for item 98 (e10)
- local match: culture-library's own extractor, chunker and matcher on the 17 backfilled EPUBs (e3) and on item 98 (e11): unmatched = 0
- culture-library: `eval generate` on live, on items 81, 98 and 73, gave unmatched = 0 for each (culture-rules#97 comment 6473927635) (e12)
- deltas: b1 (`d1`, amended), b2 (`d2`, amended), b3 (`d4`, added)
- commits: `c59bb06..b568def` (`435135e` #112, `b568def` #115)
- PRs / issues: #112, #115, #97; follow-ups #113, #114, #116, #117

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| `c5` — After each daily push, the live daily-newspaper workflow runs the sections step on the rendered EPUB and http.call PUTs the result to culture-library's `PUT /api/library/{item_id}/sources` for the item id the record step returns | medium | e10: run-498cad51, item 98, 200 · e5: revision 6. The re-run clause of o11 is unchecked (#117) |
| `c8` — The excerpt rule is step configuration (paragraphs 1, `max_chars` 300), so the library's 120-character probe stays inside one chunk | high | e3 (17 EPUBs) · e11 (item 98) · e12 (culture-library live eval) |
| `c9` — A failed manifest PUT is recorded on the run and does not fail it | high | e15: `tests/rules/test_sources_manifest_flow.py::test_a_library_answering_500_skips_the_put_step` and 3 more |
| `c10` — A one-off backfill PUTs manifests for the issues already in culture-library and lists the skipped ids | high | e2: run-a77767e1 · e18: `tests/engine/test_for_each_outcomes.py` |
| `c11` — The PUT and the fetch go through the library actor's http policy; no library URL, route or key is in code | high | e4: the live config read-back |
| `c12` — The issue's EPUB and email bytes stay byte-identical | high | e16: `tests/node/test_epub_bytes_pinned.py` |
| `c13` — The field names are the library's as shipped; the manifest is accepted (200) | high | e1, e9: live PUTs with 200 for 17 items |
| `c15` — The EPUB bytes never enter run state on the fetch path | high | e7: the stored runs read back · `tests/rules/test_sources_manifest_flow.py::test_the_stored_backfill_run_holds_no_epub_base64` |
| `c18` — culture-library's newspaper eval generator matches every manifest entry, with unmatched = 0 | high | e12: culture-rules#97 comment 6473927635 (run by culture-library, not by culture-rules) |
| `c25` — http.call resolves `<word> grant:<NAME>` headers and keeps the value out of every output | high | e13: `tests/node/test_http_grant_scheme.py` · `tests/actors/test_secrets_word_ref.py` |
| `c26` — http.call `save_to` saves the body under a listed root; outputs are status, path, sha256 and size, never the body | high | e6: the live fetch of 17 items · e17: `tests/node/test_http_save_to.py` |
| `c27` — The generic sections builtin splits EPUB, HTML and Markdown at a configured heading; every rule is config or input | medium | e14: `tests/node/test_sections.py` (77 tests); capped by lapse l1 (tests not seen failing first) |

Lapse ledger evidence:

| Lapse | Code | What |
|-------|------|------|
| `l1` | `grader-unverified` | t3's 81 tests in tests/node/`test_sections.py` passed on their first run; the task agent never saw them fail before the code existed, so the tests were not shown to be able to fail (the agent checked the real-issue manifest by hand instead) |
| `l2` | `grader-unverified` | t4 wrote the wiring before its tests (Python and vitest), so the new tests were not seen failing first; mitigation run by the main agent: with document.sections unregistered from runner.py, its registration test fails (1 failed, 85 passed) |

## Remaining Work / Follow-up

- o11's re-run clause: add a flow test for a daily re-run whose push was skipped — #117
- One no-redirect opener for http.call and `attachment.fetch` — #113
- The CI `install ffmpeg` step can hang on apt; give it a timeout — #114
- One save-to-file helper and one path-under-root check; whether builtin reads stay any-root — #116
- culture-library's scored retrieval runs on eval sets 6–8 (k=5, with and without rerank) — culture-library's side, posted there
- Close culture-rules#97 by hand once this closeout merges (the #115 body says "Refs")
