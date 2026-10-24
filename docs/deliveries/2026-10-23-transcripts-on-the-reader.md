# Delivery Summary — Transcripts on the reader

plan: `transcripts-on-the-reader` · run: `partial` · date: `2026-10-23`
baseline: `devague summary skeleton`

## Intent

> Every pocketsync recording now arrives on the Xteink reader as its own book, titled with its date and a short word, opening with a one- or two-line summary in the recording's language and then the transcript, right to left for Hebrew; past recordings are there too

The run built the six code and docs tasks on `rules/transcript-reader` (0.25.0). In t7, the
33-recording backfill ran live from a local node on the branch code (d6), and the books are
queued on the operator's reader. After the operator approved it, 0.26.0 (this branch plus the
stacked decider step, at `35ed23d`) was rolled out to all four nodes. `pocket-to-reader` is
now enabled on the engine, and an engine run proved the chain. The device sync is the
operator's, so the run is still partial.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — transcript.render built-in: a stdlib EPUB of one transcript with its summary
- `t2` — llm.complete: a second locked brief, chosen by params.brief
- `t3` — library.push: optional kind (book, article, transcript)
- `t4` — Bundle: transcript-to-reader workflow and its rule in docs/rules/pocket-transcribe, `recorded_at` passed through
- `t5` — Docs, harness files and version for transcripts on the reader
- `t6` — Cross-repo asks: xteink kind transcript, firmware folder per kind, `recording_at` on pocketsync#12
- `t7` — Live: release, enable the reader rule, backfill 33 recordings, verify on the device

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `culture_rules/node/actions/transcript_render.py`: the deterministic EPUB with the summary and the RTL/LTR choice. After review it also gained the per-transcript book cache (d4, approved). |
| `t2` | delivered | `brief: transcript` in `llm.complete`. It also gained `content_path` + `content_root` + `content_max_chars` (d2) and the empty-excerpt refusal. The catalog gained `any_of`. |
| `t3` | delivered | `library.push` `kind` (book, article, transcript). It was not test-first (l1); the control was run afterwards. |
| `t4` | delivered | The `transcript-to-reader` workflow (fallback, keep and pick, summary, render, push) and the `pocket-to-reader` rule (OR, d3, disabled, on spark). `recorded_at` passes through. The fold reads `either` (Python and TS), and the corpus went from 11 to 12. Live check fix: the summary asks for up to 2000 tokens. |
| `t5` | delivered | `docs/operations/transcripts-on-the-reader.md`, its links, the four harness files and 0.25.0 (with `release.json` and `uv.lock`). |
| `t6` | delivered | agentculture/xteink#8, with the firmware ask as a comment on it (d1), and the `recording_at` ask on agentculture/pocketsync#12. |
| `t7` | partial | The backfill ran live from a local node with the live actors and real ports (d6). Pass 1: 33 of 33 succeeded, giving 33 distinct library items, all queued on device 3. Pass 2 created 0 and gave the same 33 items and titles. 0.26.0 is rolled out (`rollout verify` passed), the bundle is imported (its actors skipped, so the live STT roots stay), and `pocket-to-reader` is enabled. On the engine, a transcription success started one reader run 3.5 s later, which queued its book 15 s after the transcription finished (e14). The operator's device check failed (e16): the books arrive, but their Hebrew text is unreadable, because the device has no Hebrew reader font. The flat list is also slow to navigate. Both are filed as agentculture/xteink#9. |

## Mid-work Decisions

- `d1` — the firmware ask is a comment on agentculture/xteink#8. Reason: agentculture/xteink-firmware is a fork of crosspoint-reader with issues disabled. Approved.
- `d2` — `llm.complete` reads the transcript excerpt itself, so the transcript text never enters run state. The short summary reply stays a step output. The operator chose this.
- `d3` — one rule `pocket-to-reader` with an OR condition, and the fold learns to read an OR of one predecessor per branch. The operator chose one rule over two.
- `d4` (approved) — `transcript.render` keeps the first book beside its transcript and returns it on later runs. Reason: a colleague review found that a re-run's different summary would make a second book. The decider (domain `general`) gave `cache_book_file` 0.93 against three other options. Logged in culture-rules#83.
- `d5` (approved) — t7 waits for a rollout approval, and the pre-PR live check runs on a local node with the push stubbed. Decider: `local_now_engine_after_merge` 0.59, `ask_then_rollout` 0.37, `rollout_branch_now` 0.04. Logged in culture-rules#83. The operator then approved the rollout ("Approve rollout"), and 0.26.0 went out from the unmerged branch head `35ed23d`.
- At import, the bundle's actors were left out: importing them would have reset the live `stt-en` and `stt-he` roots to the committed placeholders. Only the workflows and rules were imported.
- The live check found that `max_tokens: 400` left cortex-spark2 no room to answer after its reasoning (`llm_bad_response`). It is now 2000, as the newspaper uses (delta b1).
- `d6` (approved) — the backfill runs before the merge, from a local node with the live actors and real ports. Reason: the operator asked for the backfill and for live tests before the PR. Decider: backfill must wait P=0.31.
- Preparing the backfill found that the live `newspaper-library` actor has no `device_id`. The newspaper passes the `newspaper_device` variable instead, so every push would have failed. The workflow now takes `device_id`, and `pocket-to-reader` maps it from that variable (delta b3).
- The colleague review's smaller findings were fixed in the same commit (`bf039c2`):
  - an empty excerpt is refused;
  - the catalog notes `content_path` needs `content_root`;
  - the Then column words `either`;
  - the cap relationship is documented.
- `d7` (approved) — the operator's `/code-review medium` of #84 found five defects, and the operator asked for all of them fixed ("Please fix all 8", with #85's). The fixes:
  - `transcript.render` keeps and returns only a book whose summary was used, so a passing model outage no longer fixes "Summary unavailable" on a recording;
  - the title gains the local time (`<YYYY-MM-DD> <HHMM> <word>`, where the spec said `<YYYY-MM-DD> <word>`), so two recordings of one day with one word differ;
  - one 32 MiB transcript file cap for the excerpt and the book;
  - the editor picker knows `llm.complete`'s excerpt params and `brief`, `transcript.render` and `library.push` `kind`;
  - the Simple view's `either` sentence matches the API paragraph, and the chain view draws each `either` edge.

  Each new test fails against the earlier code. The 33 books already made keep their titles, because their cache is returned as before.
- `d8` (approved, the operator's) — the library moves to culture-library (library.culture.dev, culture-rules#86), and nothing about a library may be built in: "1 should be configurable, so code change to make it configurable, then configuration. We never hardcode urls." "2. kinds also should be configurable, like 1." "newspaper is public." The change (0.27.0, branch `rules/library-config`):
  - `library.push`'s actor holds `upload_path`, the `kinds` and `visibilities` it accepts, and an optional `queue_path` (unset: upload only, no device id);
  - each push names its `kind` (no built-in list, no default `article`) and may name a `visibility`; the editor offers what the chosen actor lists;
  - configuration: the library actor points at culture-library (`127.0.0.1:8796`, `grant:LIBRARY_PUSH_API_KEY`, no queue); transcripts go as `transcript`, `private`, and the newspaper as `newspaper`, `public`.
- `d9` (approved, the operator's) — the operator's `/code-review medium` of #91 found 8 issues; the operator: "fix all". The fixes:
  - the culture-rules library key is re-minted `--private` (key 3; 1 and 2 revoked), so re-pushing an item another key uploaded returns that item instead of 409;
  - the editor drops a `device_id`, `kind` or `visibility` the newly chosen actor does not use or list;
  - `library.push` ignores `device_id` again without a `queue_path` (the colleague review's "check it either way" made a hidden value fatal);
  - `workflows describe` names the kind and visibility; `_choice` takes `required=`; strings that still named xteink as the target are reworded;
  - no change for the newspaper's push fence: the migration kept xteink's item ids, so a recorded `item_id` is the same issue in culture-library.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t6` (`d1`) | agentculture/xteink-firmware has GitHub issues disabled | acceptable |
| `t4` (`d2`) | the excerpt would otherwise be stored in every run; the operator chose to keep the transcript out and allow the summary | acceptable |
| `t4` (`d3`) | the fold read the OR rule as continuing from every workflow; the operator chose one rule with OR | acceptable |
| `t1` (`d4`, approved) | re-runs could create a second book; the render now writes a cache file into the transcripts folder | acceptable |
| `t7` (`d5`, `d6`, approved) | rollouts need the operator; the backfill ran live from a local node, and the engine rule went live after the approved rollout; the device sync is still pending | needs-follow-up |
| `t4` | `device_id` input mapped from `newspaper_device` (b3) | acceptable |
| `t2` | `max_tokens` 400 → 2000 after the live check | acceptable |
| `t1` (`d7`, approved) | the review fixes: the summary-only cache, the title's local time, one file cap | acceptable |
| `t4` (`d7`, approved) | the review fixes in the editor: the picker's params and kinds, the `either` sentence and chain edges | acceptable |
| `t3` (`d9`, approved) | the #91 review fixes: moot params dropped in the editor, `device_id` ignored without a queue, the key minted `--private` (b6) | acceptable |
| `t3` (`d8`, approved) | the library is configuration on the actor, and the push goes to culture-library with no device queue (b4, b5); b3's device queue now applies only to a library actor with a `queue_path` | acceptable |

## Evidence

- tests: the full suite at `08e4d15` passed with 7073 passed and 0 failed (`uv run pytest -n 4`).
- After the review fixes and the live fix, 363 tests in the evidence files passed at `e1f4a31`:
  - `tests/node/test_transcript_reader_flow.py`, `test_transcript_render.py`, `test_transcript_excerpt.py`;
  - `test_llm_complete.py`, `test_library_push.py`;
  - `tests/rules/test_pocket_transcribe_bundle.py`, `tests/model/test_rule_paragraph.py`.
- On the stacked decider branch, the full suite has 7192 passed. Its one failure was a local `web/node_modules` symlink, which was then removed, and the test passed.
- web: `npx tsc --noEmit` clean; `vitest` 813 of 813 (`--maxWorkers=4`).
- lint: black, isort, flake8, bandit, `scripts/scan-secrets.py` clean; markdownlint clean; `teken cli doctor --strict` green; harness-smoke 6 of 6.
- Sensitivity checks:
  - `test_a_rerun_whose_summary_answers_differently_still_adds_no_second_book` fails with the pre-d4 `transcript_render.py`;
  - the `library.push` kind tests fail (9) against the pre-t3 `library.py`.
- live (local node, push stubbed): one real Hebrew recording, 2 runs.
  - Both succeeded, the summary was used, and the title has the `<date> <word>` shape.
  - The book is RTL, and both runs pushed the same bytes. Each run took 39–46 s.
- live (deployed engine 0.26.0, after the rollout): `rollout verify` passed (4 nodes on 0.26.0, API 0.26.0, engine running, the 2 snapshot rules enabled). Then `transcribe-file-he` was run directly for recording `a41fdac7bee2`, whose result was already on disk (run `run-1ac3947609254ae19e941f1a71d44256`). `pocket-to-reader` started exactly one reader run (`run-75def4e5483f342bc068c65b612f978f`), with every step on spark. It gave the same book (item 29, the same title), `created: false`. Device 3's queue holds the 33 transcript entries plus the day's newspaper.
- d8 (`ecd0cb4` on `rules/library-config`, 0.27.0):
  - the full suite passed with 7256 passed and 9 skipped; vitest 827 of 827, tsc clean, e2e `workflows-described` 19 of 19; all lint gates clean;
  - sensitivity: 40 of the 138 `test_library_push.py` tests fail against `origin/main`'s `library.py` (e17);
  - live: 0.27.0 rolled out with the operator's approval (`rollout verify` passed: 4 nodes and the API on 0.27.0, the 3 snapshot rules enabled). The live actor points at culture-library, `daily-newspaper` (rev 5) has its push enabled again as `newspaper`/`public`, and `transcript-to-reader` (rev 2) pushes `transcript`/`private`. No live push has run yet: the first is the next 05:00 newspaper.
- devague: obligations o1–o14 and evidence e1–e15 are filed and approved by the operator, with d4–d6 and b1–b3. e16 (proposed) records the operator's failed device check for o13. d8, e17–e19, b4 and b5 are approved; d9, e20–e22 and b6 are approved.
- commits: `062c9c8..1883d88` on `rules/transcript-reader`.
- issues: culture-rules#83 (the decisions log), #82 (lighter test runs), agentculture/xteink#8, agentculture/pocketsync#12.

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| A transcription success starts exactly one reader run, with the right lane per language | high | e1, e2 |
| The book is a deterministic EPUB titled `<YYYY-MM-DD> <word>` with the Israel date, right to left for Hebrew | high | e3, e6 |
| The summary uses the locked transcript brief, and the transcript never enters run state | high | e4, e10 |
| A failed summary still ships the book | high | e5 |
| A re-run never makes a second book, even with a different summary | high | e9 (sensitivity) |
| `library.push` takes `kind` | medium | e7: capped by l1 (not test-first; the control was run afterwards) |
| `library.push`'s library, kinds and visibilities are the actor's configuration; transcripts go to culture-library as private `transcript` items | high | e17 (sensitivity), e18, e19; live push not yet observed |
| The cross-repo asks exist | high | e8 |
| A real recording's summary and book work against the live model | high | e12 (backfill, real ports) and e14 (the deployed engine) |
| All 33 past recordings are in the reader's library and queue, with 0 duplicates after a re-run | high | e12, e13, e14: observed live (33 items, the device 3 queue holds them, pass 2 and the engine re-run created 0) |
| A new recording's book is queued within 10 minutes of its transcription | medium | e14: on the engine, 15 s from a transcription success to the queued book; shown with an already-transcribed recording, because the STT rules stay off until pocketsync#12 |
| Books appear on the device and read right to left | low | e16 (fail): the books arrive, but the Hebrew text is unreadable; the device has no Hebrew reader font (agentculture/xteink#9) |

## Remaining Work / Follow-up

- agentculture/xteink#9: a Hebrew-capable reader font, so Hebrew books render right to left (o13 fails until then). With d8 the books reach the reader through culture-library (the reader pulls); the send-to-device rule is culture-rules#86 item 2. Owner: xteink, then culture-rules.
- Observe the first live push (the next 05:00 newspaper) and the first new transcript. The 33 migrated transcript items are `document` in culture-library; retagging them is culture-library's side.
- agentculture/pocketsync#12: the landed event with `recording_at`; then enable the STT rules (item 6 of the earlier plan).
- culture-rules#82: lighter local test runs, and cleanup of orphaned test rigs.
- Whether the device is a mirror device (v1, parked).
