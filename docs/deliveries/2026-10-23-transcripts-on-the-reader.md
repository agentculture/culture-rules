# Delivery Summary — Transcripts on the reader

plan: `transcripts-on-the-reader` · run: `partial` · date: `2026-10-23`
baseline: `devague summary skeleton`

## Intent

> Every pocketsync recording now arrives on the Xteink reader as its own book, titled with its date and a short word, opening with a one- or two-line summary in the recording's language and then the transcript, right to left for Hebrew; past recordings are there too

The run built the six code and docs tasks on `rules/transcript-reader` (0.25.0). In t7, the
33-recording backfill ran live from a local node on the branch code (d6), and the books are
queued on the operator's reader. The rule on the deployed engine waits for a rollout (d5),
and the device sync is the operator's. This makes the run partial.

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
| `t1` | delivered | `culture_rules/node/actions/transcript_render.py`: the deterministic EPUB with the summary and the RTL/LTR choice. After review it also gained the per-transcript book cache (d4, proposed). |
| `t2` | delivered | `brief: transcript` in `llm.complete`. It also gained `content_path` + `content_root` + `content_max_chars` (d2) and the empty-excerpt refusal. The catalog gained `any_of`. |
| `t3` | delivered | `library.push` `kind` (book, article, transcript). It was not test-first (l1); the control was run afterwards. |
| `t4` | delivered | The `transcript-to-reader` workflow (fallback, keep and pick, summary, render, push) and the `pocket-to-reader` rule (OR, d3, disabled, on spark). `recorded_at` passes through. The fold reads `either` (Python and TS), and the corpus went from 11 to 12. Live check fix: the summary asks for up to 2000 tokens. |
| `t5` | delivered | `docs/operations/transcripts-on-the-reader.md`, its links, the four harness files and 0.25.0 (with `release.json` and `uv.lock`). |
| `t6` | delivered | agentculture/xteink#8, with the firmware ask as a comment on it (d1), and the `recording_at` ask on agentculture/pocketsync#12. |
| `t7` | partial | The backfill ran live from a local node with the live actors and real ports (d6). Pass 1: 33 of 33 succeeded, giving 33 distinct library items, all queued on device 3. Pass 2 created 0 and gave the same 33 items and titles. Still pending: enabling `pocket-to-reader` on the engine (a rollout, d5) and the device sync (the operator). |

## Mid-work Decisions

- `d1` — the firmware ask is a comment on agentculture/xteink#8. Reason: agentculture/xteink-firmware is a fork of crosspoint-reader with issues disabled. Approved.
- `d2` — `llm.complete` reads the transcript excerpt itself, so the transcript text never enters run state. The short summary reply stays a step output. The operator chose this.
- `d3` — one rule `pocket-to-reader` with an OR condition, and the fold learns to read an OR of one predecessor per branch. The operator chose one rule over two.
- `d4` (proposed) — `transcript.render` keeps the first book beside its transcript and returns it on later runs. Reason: a colleague review found that a re-run's different summary would make a second book. The decider (domain `general`) gave `cache_book_file` 0.93 against three other options. Logged in culture-rules#83.
- `d5` (proposed) — t7 waits for a rollout approval, and the pre-PR live check runs on a local node with the push stubbed. Decider: `local_now_engine_after_merge` 0.59, `ask_then_rollout` 0.37, `rollout_branch_now` 0.04. Logged in culture-rules#83.
- The live check found that `max_tokens: 400` left cortex-spark2 no room to answer after its reasoning (`llm_bad_response`). It is now 2000, as the newspaper uses (delta b1).
- `d6` (proposed) — the backfill runs before the merge, from a local node with the live actors and real ports. Reason: the operator asked for the backfill and for live tests before the PR. Decider: backfill must wait P=0.31.
- Preparing the backfill found that the live `newspaper-library` actor has no `device_id`. The newspaper passes the `newspaper_device` variable instead, so every push would have failed. The workflow now takes `device_id`, and `pocket-to-reader` maps it from that variable (delta b3).
- The colleague review's smaller findings were fixed in the same commit (`bf039c2`):
  - an empty excerpt is refused;
  - the catalog notes `content_path` needs `content_root`;
  - the Then column words `either`;
  - the cap relationship is documented.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t6` (`d1`) | agentculture/xteink-firmware has GitHub issues disabled | acceptable |
| `t4` (`d2`) | the excerpt would otherwise be stored in every run; the operator chose to keep the transcript out and allow the summary | acceptable |
| `t4` (`d3`) | the fold read the OR rule as continuing from every workflow; the operator chose one rule with OR | acceptable |
| `t1` (`d4`, proposed) | re-runs could create a second book; the render now writes a cache file into the transcripts folder | acceptable |
| `t7` (`d5`, `d6`, proposed) | rollouts need the operator; the backfill ran live from a local node, and the engine rule and the device sync are still pending | needs-follow-up |
| `t4` | `device_id` input mapped from `newspaper_device` (b3) | acceptable |
| `t2` | `max_tokens` 400 → 2000 after the live check | acceptable |

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
- devague: obligations o1–o14 and evidence e1–e11 are filed, all proposed. o12–o14 have no evidence yet: they wait for t7.
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
| The cross-repo asks exist | high | e8 |
| A real recording's summary and book work against the live model | medium | the local live check (d5): not the deployed engine, push stubbed |
| All 33 past recordings are in the reader's library and queue, with 0 duplicates after a re-run | high | e12, e13: observed live (33 items, the device 3 queue holds them, pass 2 created 0) |
| A new recording's book is queued within 10 minutes of its transcription | unverified | needs the engine rule (a rollout, d5) |
| Books appear on the device and read right to left | unverified | the operator's device sync (o13) |

## Remaining Work / Follow-up

- t7: roll out 0.26.0 (both features), then import the bundles and enable `pocket-to-reader`, so new recordings get their books. Owner: the operator's approval, then Claude.
- Sync the reader and check the 33 books, Hebrew right to left (o13). Owner: the operator.
- Adjudicate the proposed records: d4, d5, d6, o1–o14, e1–e13, b1–b3. Owner: the operator.
- agentculture/xteink#8: once the `transcript` kind and the firmware folder ship, switch the push to `kind: transcript`.
- agentculture/pocketsync#12: the landed event with `recording_at`; then enable the STT rules (item 6 of the earlier plan).
- culture-rules#82: lighter local test runs, and cleanup of orphaned test rigs.
- Whether the device is a mirror device (v1, parked).
