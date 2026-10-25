# Delivery Summary — Gwen reads the library

plan: `gwen-reads-the-library` · run: `partial` · date: `2026-10-24`
baseline: `devague summary skeleton`

## Intent

> Gwen learns only from the library: culture-rules ships the generic code to put transcripts, newspapers and to-dos into culture-library as items that are kept current under the same id, and the Gwen-specific flows are configured live

After: each new Pocket recording is transcribed on its own. culture-library gets a Markdown
`transcript` item (summary and key facts, then the transcript) and, when the recording has
action items, a `todo` checklist item. When Gwen marks a to-do done, the checklist item is
replaced under the same id, and everything Gwen knows is in the library.

This run built the generic code (t1–t8) on branch `rules/gwen-library`. The live
configuration and its validation (t9) came after the merge (PR #93, `48e58ee`) and the 0.28.0
rollout, as the plan orders them.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — library.push uploads any format the actor lists and replaces an item by id
- `t2` — llm.complete gains the locked recording brief and an answer cache beside its content file
- `t3` — transcript.render can write the transcript as Markdown with summary and key facts
- `t4` — checklist builtins: merge, set, render and bind over a stored list
- `t5` — json.read builtin reads named fields from a JSON file under a root
- `t6` — audio.transcribe hashes the audio itself and fails a missing file finally
- `t7` — wire the new params and builtins: runner registration, action catalog, describe, editor
- `t8` — docs, harness files, changelog and the version bump
- `t9` — after merge and rollout: configure the Gwen flows live and validate them

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `culture_rules/node/actions/library.py`: the actor's `formats` (md, txt, json, epub, pdf) and a step `format`; replace by `item_id` through `replace_path` + `replace_method`. Commit `d60c303`, merged in `0a6dd4a` |
| `t2` | delivered | `RECORDING_BRIEF`, `recording_answer.py` (`parse_recording_answer`), and `cache: true` storing `answer.recording.<sha16>.json`. Commit `2a52718`, merged in `5324f27` |
| `t3` | delivered | `transcript.render` `format: md` with the title, summary, key facts and transcript; rtl div; HTML-escaped text; md cache. Commits `ba77377` and `d371de8`, merged in `05293d2` |
| `t4` | delivered | `checklist.py`: `checklist.merge`, `set`, `render` and `bind` over the `checklists` collection, with CAS retries. Commit `771b1e2`, merged in `9a6ffa0`; changed further in t7 (`d1`, approved) |
| `t5` | delivered | `json_read.py`, the `json.read` builtin. Commit `15c323d`, merged in `b2e76ea` |
| `t6` | delivered | `stt.py`: `audio_sha256` is optional (the file is hashed in chunks), and a missing file gives `audio_missing` (final). Commits `68ffeb7` and `1e00649`, merged in `883e649` |
| `t7` | delivered | Wiring in commit `d4a48ea`: runner registration, catalog params, describe glosses, the editor, and `tests/node/test_library_items_flow.py`. Then `f8a6821` and `86ab3b0`: bind takes an int `item_id`, render returns `item_id` |
| `t8` | delivered | `docs/operations/library-items.md` plus updates to `library.md`, `llm.md`, `pocket-transcribe.md`, the four harness files, 0.28.0 and `release.json`. Commit `f2a3767` |
| `t9` | partial | 0.28.0 rolled out (verified on 4 nodes). Live, outside the repo: `read-recording` (json.read on `meta.json`) and rule `pocket-recording-created` (enriched, change created); both transcription rules now follow it, building the audio's absolute path with a `{{ }}` template; `transcript-to-reader` uses the recording brief with the cache and pushes Markdown; `recording-todos` and `todo-flip` (rules enabled); the library actor lists `md`, `todo` and culture-library 0.12.0's replace route; the library key rotated to one that can write `todo` (key 6). Validated: a new recording went from its created event to transcript item 58 in about 70 s with no manual step (e22); hand-started runs gave transcript item 57, to-do item 59, and a flip replacing item 59 in 2 s (e17, e20, e21). Not yet seen: a real `embodiment.bus.todo` event, and a rule-fired to-do item (the first natural recording had no action items) |

## Mid-work Decisions

- `d1` (approved by the operator) — t7 changed t4's module: `checklist.render` also
  returns `item_id`, and `checklist.bind` accepts a positive int `item_id`. The reason is that
  wiring render → library.push (replace the bound item) → bind needs the bound id before the
  push, and library.push uses an int. The decider was consulted: record_deviation_keep 0.88,
  needs_follow_up 0.55 vs acceptable 0.38.
- `d2` (approved by the operator) — after the PR opened, culture-library#12 settled the
  replace route as `PUT /api/library/{id}/file`, answering with the item itself rather than
  `{item: {id}}`. t1 had assumed the wrapped shape, so every live replace would have failed
  `bad_response`. library.push now accepts either shape for a replace (commit `d0d51d7`, tests
  on culture-library's real shape); an upload still needs `{item: {id}}`.
- `d3` (approved by the operator) — `/code-review medium` on #93 found that
  `transcript.render` could not read `llm.complete`'s recording answer (its `summary` is a list
  of lines), so every live transcript item would have lost its summary. It also found that a
  string `bind` broke the next push, that an out-of-range `at` made a list unrenderable, that
  cache hits charged tokens twice, and that a `StoreError` escaped the checklist's retries.
  Each was fixed with a failing-first test (commit `b019be9`). The answer cache staying keyed by
  content, not model, is kept by design and documented.
- t9 kept the plan's order (after merge + rollout). The decider had leaned `partial_before_pr`
  (0.67); without an approved pre-merge rollout, no deviation was recorded.
- `d4` then `d5` (both the operator's) — during t9 the enriched event turned out to name the
  audio only relative to pocketsync's files root, while `audio.transcribe` needs an absolute
  path. I first told the operator no mapping could join the two, and they chose a code fix
  (`d4`, PR #94). That premise was wrong: rule mappings and action-step params substitute
  `{{ }}` templates (`culture_rules/model/refs.py`). The operator then chose to close #94
  unmerged and build the path with a template; `d5` withdraws `d4`. No code changed.
- The library key could not be rotated by the assistant (the secret-store write is the
  operator's); the operator ran the rotation script, and key 6 replaced key 3.
  agentculture/culture-library#14 asks for a `keys rotate` verb so this is one command.
- t3 was sent back once to HTML-escape transcript- and model-derived text in the Markdown,
  because inside the rtl `<div>` a stray `<` could be read as HTML (commit `d371de8`).
- The Gwen and embodiment names were removed from code and tests: plan-slug references became
  "library items" and test actors became `assistant`/`operator` (c19). Two pre-existing lines on
  `main` in `tests/model/test_describe.py` are untouched.
- Task agents decided several things the plan left open, each filed as a delta (approved; `b4` rejected as a duplicate of `b5`):
  - checklist ids are 1-based (`b5`);
  - the Markdown render is cached like the EPUB (`b1`);
  - the answer cache is allowed only with the recording brief and a `content_path` (`b2`);
  - an actor with no `formats` accepts only epub (`b3`).
- Task branches were `rules/gl-tN` in `../.worktrees.culture-rules/gl-tN`, per the repo's
  convention, not the skill's `agent/` prefix.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t4` / `t7` (`d1`) | Wiring needed render to return the bound `item_id` and bind to accept library.push's int id; t7 changed t4's file. Delta `b6` | needs-follow-up |
| `t1` (`d2`) | The replace reply shape was guessed before culture-library defined it; fixed to accept the item itself. Evidence `e16`, delta `b7` | acceptable |
| `t2` / `t3` / `t4` (`d3`) | Review fixes: render reads the list summary; cache usage, chunked hash; checklist `at` range, string `item_id`, `StoreError` | acceptable |
| `t9` (`d4`, withdrawn by `d5`) | A relative audio path in the event: bridged in configuration with a `{{ }}` template, no code change | acceptable |
| `t9` | A real reader event, and a rule-fired to-do item, are still to be seen | needs-follow-up |

No other task drifted: t1–t3, t5, t6 and t8 delivered against their acceptance criteria (Actual
Delivery above).

## Evidence

- tests, at `f2a3767` (filed as e1–e13):
  - `tests/node/test_library_push.py` — pass (20 format, 30 replace);
  - `tests/node/test_transcript_render.py` md tests — pass (9);
  - `tests/node/test_llm_complete.py` recording and cache tests — pass (19, 7);
  - `tests/node/test_checklist.py` — pass (57: merge, set, render, bind, 5 concurrency);
  - `tests/node/test_json_read.py` — pass (10);
  - `tests/node/test_audio_transcribe.py` hash and missing tests — pass (8, 5);
  - `tests/node/test_library_items_flow.py` and `tests/node/test_default_ports.py` — pass (15).
- Sensitivity, measured by the task agents against `main`'s modules:
  - t1: 43 new tests fail;
  - t2: 9 still fail with a stub brief;
  - t3: 8 of 10 fail, and the escape test failed first;
  - t6: 3 of 5 fail.
- Full suite at `f2a3767`: 7429 passed, 9 skipped; at `b019be9` (after the review fixes): 7450 passed, 9 skipped. vitest: 834 passed. tsc: clean.
- Lint: black, isort, flake8 and bandit clean; markdownlint `**/*.md` 0 errors; scan-secrets
  clean; `teken cli doctor --strict` exit 0; harness-smoke 6/6.
- Observation (e14): the branch's diff adds 0 lines naming a person.
- Observation (e15): a read-only scan of 49 live definitions, plus the repo, finds 0 embodiment
  ingest calls (2026-10-24T22:18Z).
- commits: `main..f2a3767` on `rules/gwen-library`.
- issues: agentculture/culture-library#10.

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| library.push uploads a listed format and replaces an item by id through actor config | high | e1, e2 (sensitivity) · commit `d60c303` |
| the recording brief returns a checked {summary, word, facts, action_items} and its answer is cached per transcript | high | e5 (sensitivity), e6 · commit `2a52718` |
| transcript.render writes the Markdown transcript item | high | e4 (sensitivity) · commits `ba77377`, `d371de8` |
| the checklist steps keep ids and done marks, apply only newer flips, never lose a flip under contention, and detect a stale push | medium | e7–e10 (execution; new module, no before/after control) · commit `771b1e2` |
| json.read reads scalar fields under a root and refuses everything else | medium | e11 (execution) · commit `15c323d` |
| audio.transcribe works without a given hash and fails a missing file finally | high | e12, e13 (sensitivity) · commits `68ffeb7`, `1e00649` |
| the new pieces are registered and run together on a node | medium | `tests/node/test_library_items_flow.py`, `tests/node/test_default_ports.py` · commit `d4a48ea` |
| the PR names no person; culture-rules runs no embodiment ingest | high | e14, e15 |
| a recording becomes a Markdown transcript item and, with action items, a to-do item, live | medium | e22: item 58 from a new recording with no manual step; e17, e20: items 57 and 59 from hand-started runs; a rule-fired to-do item not yet seen |
| a flip replaces the to-do item under the same id within 60 s; re-extraction keeps ids and done marks | medium | e21: 2 s, item 59, by a direct `todo-flip` run; the reader's event path is not yet exercised |
| the live newspaper push sends kind newspaper with links | medium | e18: item 55, public, 20 links, pushed under 0.27.0 |
| no live definition calls embodiment memory ingest, after t9 | high | e19: 19 workflows, 33 rules, 21 actors |

Lapse ledger evidence (approved):

| Lapse | Code | What |
|-------|------|------|
| `l1` | grader-unverified | t6's agent ran bandit on `stt.py` but did not read its output before committing |

It touches no claim's confidence above: bandit was run again on `stt.py` at the t6 merge and
is clean, and the full-tree bandit gate passes.

## Remaining Work / Follow-up

- `t9` — watch the first new recording with action items get its to-do item from the rule, and the
  first real `embodiment.bus.todo` flip (needs the reader to send checklist ids, r2); file the
  evidence. Owner: main agent.
- Revoke the old library key 3 (`keys revoke --id 3`) once nothing else uses it. Owner: operator.
- Records: none await the operator. Approved: deviations `d1`–`d5`
  (`d4` withdrawn by `d5`), obligations `o1`–`o18`, evidence `e1`–`e22`, deltas `b1`–`b3` and
  `b5`–`b7`, lapse `l1`; `b4` rejected (a duplicate of `b5`).
- Embodiment (risk r2): Gwen reads to-dos from the library and sends the checklist comment id in
  `embodiment.bus.todo`.
- Re-pushing the 33 old transcripts as Markdown items (v2) — live, after t9.
