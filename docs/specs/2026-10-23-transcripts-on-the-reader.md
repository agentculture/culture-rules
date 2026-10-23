# Transcripts on the reader

> Every pocketsync recording now arrives on the Xteink reader as its own book, titled with its date and a short word, opening with a one- or two-line summary in the recording's language and then the transcript, right to left for Hebrew; past recordings are there too

## Audience

- The operator, reading past Pocket recordings on the Xteink reader; and the culture-rules engine that builds and pushes each book
  - instruction: check docs/operations/transcripts-on-the-reader.md addresses the operator (setup, backfill, reading) and the engine bundle ships in docs/rules/

## Before → After

- Before: Transcripts exist only as JSON files under the transcripts folder, readable through a local page on spark; nothing reaches the reader, and Pocket's own title and summary come from its weaker transcript
  - instruction: compare against docs/operations/pocket-transcribe.md as of 0.24.2: no library.push in the pocket-transcribe bundle
- After: Each recording is one EPUB on the Xteink reader named '<YYYY-MM-DD> <word>', opening with a 1-2 line summary in its language and then the transcript (right to left for Hebrew); all past recordings are there too
  - instruction: on the device: the books appear after a sync; in xteink: GET /api/library lists one item per recording

## Why it matters

- The operator can read and search past conversations away from the desk, in a book format, with a dated name and a summary that says what each one was about
  - instruction: the operator reads three backfilled books on the device and reports whether the name and summary identify the recording

## Requirements

- A new rule fires on rules.run.succeeded (d21, `culture_rules`/node/`run_events.py`) from the transcribe workflows and starts a generic transcript-to-reader workflow, with the run's exported outputs (`result_path`, `audio_sha256`, language, model, `window_count`, `empty_window_count`) as inputs
  - honesty: a rules.run.succeeded event from transcribe-file-he (or transcribe-file for en) starts exactly one transcript-to-reader run, and a failed or cancelled transcription starts none
- Best lane per language: a Hebrew or unlabelled recording's book uses the zipformer transcript (transcribe-file-he); an en recording's uses whisper (transcribe-file), falling back to zipformer when every whisper window came back empty; the rule's condition picks which run starts the book, so one recording yields one book
  - honesty: for an unlabelled or he recording only the zipformer success starts a book; for en only the whisper success does, and with every whisper window empty the run transcribes with stt-he before rendering
- A new stdlib built-in, transcript.render, reads the schema-1 transcript file only under the transcripts root and builds a deterministic EPUB (fixed zip times, so the same input gives the same bytes): title '<YYYY-MM-DD> <word>', the summary first, then the transcript; Hebrew is lang he, dir rtl, page-progression-direction rtl. `newspaper_render.py` stays as it is (English-only, newspaper-shaped)
  - honesty: rendering the same transcript, summary and date twice gives byte-identical EPUBs; a he book has lang/xml:lang he, dir rtl and page-progression-direction rtl; a path outside the transcripts root is refused
- llm.complete gains a second locked brief chosen by a fixed param (e.g. brief: transcript), still with no free-text system prompt and no tools; the transcript brief asks for a 1-2 line summary in the transcript's language and one short title word in Latin letters, as JSON
  - honesty: llm.complete with brief transcript sends the transcript brief as the system message and nothing else from the step; without brief it still sends `NEWSPAPER_BRIEF`; an unknown brief value is refused before any request
- The summary step tolerates failure (config.`on_error`: skip, like the newspaper): the book still ships, titled '<date> recording' with a 'Summary unavailable.' line; a reply that is not 1-2 lines or whose word is not a short Latin word is treated as a failure
  - honesty: with the model unreachable, or a reply that is 3+ lines or has a non-Latin or multi-word title word, the book still ships titled '<date> recording' with 'Summary unavailable.'
- The date is the Asia/Jerusalem local date of Pocket's `recording_at`: pocketsync's meta.json has it, pocketsync#12's event does not, so the event asks for it (a comment on pocketsync#12) and the transcribe workflows pass it through as an optional input and output
  - honesty: a `recording_at` of 2026-10-23T22:30:00Z (01:30 Israel time) gives the date 2026-10-24; with no `recording_at` the run fails clearly rather than inventing a date
- library.push takes an optional kind (book, article, transcript); the book is pushed as kind transcript once xteink accepts it, as article until then, landing flat in /xteink/ named after its title (xteink-firmware SyncProtocol.cpp bookFileName = sanitized title + .epub)
  - honesty: library.push without kind still uploads article (the newspaper unchanged); kind transcript is sent as given; an unknown kind is refused before any request
- Cross-repo issues: agentculture/xteink accepts library kind transcript (the upload enum is book|article today); agentculture/xteink-firmware places a non-book kind under `/xteink/<kind>s/` (XteinkSync.cpp placeFile puts everything in kBooksDir /xteink)
  - honesty: both issues exist, are self-contained, and quote the current enum and placeFile code they ask to change
- Backfill: every existing recording (33 today) gets its book, oldest first by `recording_at`, through workflows run with the inputs read from pocketsync's meta.json and the existing transcript files
  - honesty: the backfill starts one run per recording ordered by `recording_at`, and every run's book lands
- The reader rule and every step that reads transcripts or pushes run on spark (placement machine spark, as pocket-transcribe does): the transcript files, pocketsync's meta.json and xteink at 127.0.0.1:8780 are all local to spark
  - honesty: the bundle's reader rule has placement machine spark, and a test run on a node other than spark is never handed its steps

## Honesty conditions

- a recording that lands with the rules enabled ends as one queued library item titled with its local date, without any manual step
- a second backfill run leaves the library item count unchanged (every upload answers created: false)
- the run's outputs, step outputs and logs hold no transcript or summary text: only paths, hashes, ids, counts and the title
- the operations doc covers setup, backfill and reading for the operator; the bundle ships under docs/rules
- as of 0.24.2 no bundle pushes a transcript to xteink
- after the backfill the device shows one dated book per recording, Hebrew ones right to left
- the operator reads backfilled books on the device and confirms the names and summaries identify the recordings
- 33 items for 33 recordings, 0 new after a re-run, and a new recording's book queued within 10 minutes of its transcription succeeding

## Success signals

- After the backfill, 33 of 33 existing recordings have exactly 1 library item each (0 duplicates after a second backfill run), and a new recording's book is queued within 10 minutes of its transcription run succeeding
  - instruction: count xteink library items titled with a date per audio sha from the run outputs; re-run the backfill and recount; time the rules.run.succeeded event against the push step's finish

## Scope / boundaries

- One book per recording: a repeat (redelivered event, retried run, backfill re-run) pushes the same bytes, which xteink deduplicates by sha256 (library.py), so the reader never gets a second copy
- Transcript text goes only to the local summary model and the operator's own xteink library: run outputs stay scalars, and no speech text goes into issues, docs, logs or claude.ai

## Non-goals

- No change to the STT lanes or their rules: pocket-transcribe and pocket-transcribe-he stay disabled until pocketsync#12 ships the landed event

## Assumptions

- The summary and title word come from our own transcript, not from Pocket's title or summary.md (made from Pocket's weaker transcript)
- The summary runs on cortex-spark2, the newspaper's local lane actor (one actor per model lane), not a new task-named actor

## Scope exploration

- `s1` — `culture_rules/node/run_events.py`: d21: every terminal run emits rules.run.succeeded with the `workflow_id` and only the explicitly exported outputs, so a plain event trigger with a condition can start the reader workflow
  - seeds: `c2`, `c3`
- `s2` — `docs/rules/pocket-transcribe/workflows/transcribe-file.json`: exports `result_path`, `audio_sha256`, model, language, `segment_count`, `window_count`, `empty_window_count`; no recording date in its inputs or outputs
  - seeds: `c2`, `c3`, `c7`
- `s3` — `culture_rules/node/actions/transcript_file.py + stt.py`: schema-1 file at `<output_dir>/<sha>/transcript.<model-slug>.json` with segments \[{start,end,text}\] and `created_at` (transcription time, not recording time); outputs are scalars only
  - seeds: `c4`, `c12`
- `s4` — `culture_rules/node/actions/newspaper_render.py`: the only EPUB builder: newspaper-shaped (sections, link-checked summaries), lang and dc:language hard-coded en, no rtl
  - seeds: `c4`
- `s5` — `culture_rules/node/actions/llm.py`: system message is always the locked `NEWSPAPER_BRIEF`; every other param ignored; content capped at `DEFAULT_MAX_CONTENT_CHARS` 48000, `max_tokens` default 1500
  - seeds: `c5`, `c6`
- `s6` — `culture_rules/node/actions/library.py`: uploads with kind hard-coded article; xteink dedups uploads by sha256 (created: false) and re-queueing a queued item returns the existing entry
  - seeds: `c8`, `c11`
- `s7` — `xteink server /openapi.json (127.0.0.1:8780)`: POST /api/library takes file, title, author, kind; kind enum is book|article; no folder field
  - seeds: `c8`, `c9`
- `s8` — `xteink-firmware src/xteink/XteinkSync.cpp, SyncProtocol.cpp`: placeFile moves every delivered file into kBooksDir /xteink, named bookFileName(title) = sanitized title + extension; kind is not used for placement
  - seeds: `c8`, `c9`
- `s9` — `pocketsync files/<yyyy>/<mm>/<id>/meta.json`: has `recording_at` (UTC), language, duration, files.audio.sha256, plus Pocket's own title and summary.md
  - seeds: `c7`, `c10`, `c14`
- `s10` — `agentculture/pocketsync#12 body`: the landed event's data fields are `audio_path`, `audio_sha256`, language, `recording_id` (+ optional `content_hash`, `updated_at`); `recording_at` is not among them
  - seeds: `c7`
- `s11` — `docs/rules/newspaper/actors/cortex-spark2.json`: the newspaper summarises on the cortex-spark2 lane actor via llm.complete
  - seeds: `c15`
- `s12` — `challenge pass / adjacent-systems lens: docs/rules/pocket-transcribe/rules/pocket-transcribe.json placement`: the STT rules are placed on machine spark and the newspaper's library actor is 127.0.0.1:8780; the reader must be placed the same way or a node elsewhere cannot read the files
  - seeds: `c22`
- `s13` — `challenge pass / failure-mode lens: culture_rules/node/actions/library.py + xteink 8780`: xteink down: upload fails retryable (5xx/network), the run fails visibly after its retries and a re-run repeats the same bytes (dedup); no partial book
- `s14` — `challenge pass / collision lens: xteink-firmware SyncProtocol.cpp bookFileNameWithId`: two recordings with the same date and word: the second file is named `<title> (<id>).epub`, never overwriting the first
- `s15` — `challenge pass / reversibility lens: xteink-firmware docs/xteink/sync.md step 5 + DELETE /api/library/{id}`: a bad book is removed by deleting the library item; on a mirror device the sync then deletes only files the server delivered and whose hash still matches; whether the operator's device is in mirror mode was not checked
- `s16` — `challenge pass / chaining lens: culture_rules/events/emit.py MAX_EVENT_HOPS = 8, run_completions.py`: landed event -> transcribe run -> rules.run.succeeded -> reader run is 2-3 hops, inside the cap of 8; no lineage trust check applies (lineage.py's `workflow_not_trusted` gates only the pr-fix review/retry chain)
- `s17` — `challenge pass / data-flow lens: pocketsync language field`: an unlabelled recording that is really English gets the zipformer (Hebrew) transcript, per the operator's best-lane choice c3; accepted, not re-opened
- `s18` — `challenge pass / security lens: xteink.culture.dev (operator, 2026-10-24)`: not publicly accessible: the endpoint is protected with a secret (operator's statement), so transcript books syncing through it was accepted (q4 a)
  - seeds: `q4` (question, resolved)

## Decisions

- Operator 2026-10-24: the reader workflow runs right after the transcription workflow finishes (fired by its rules.run.succeeded event, c2), not on a schedule or a separate pass

## Hard questions

- A new en recording whose whisper windows are all empty has no zipformer transcript (pocket-transcribe-he skips en): should the reader workflow run zipformer itself as the fallback, or ship the book with 'No speech recognised'? (resolved: Operator 2026-10-24 (confirmed all, recommended answer): the reader workflow runs zipformer itself as the fallback for an en recording whose whisper windows are all empty)
- If the zipformer run fails for a Hebrew or unlabelled recording (thor or its tunnel down, as after tonight's reboot), whisper's transcript exists but c3 starts no book: should a rules.run.failed of transcribe-file-he start the book from whisper instead, or wait for a re-run? (resolved: Operator 2026-10-24: (b) a failed zipformer run starts no book; the book waits for a re-run of transcribe-file-he)
- A long recording's transcript exceeds llm.complete's 48000-character content cap (the 56-minute benchmark is near it): summarise from the first 48000 characters, or summarise per window and join? (resolved: Operator 2026-10-24 (confirmed all, recommended answer): summarise from the first 48000 characters; none of the 33 today exceeds it (max 29744))
- Away from home the device syncs through xteink.culture.dev (a Cloudflare tunnel, xteink-firmware docs/xteink/sync.md server order), so a book's text crosses Cloudflare (TLS ends there). The newspaper is public; transcripts are private conversations. Accept that, or keep transcript kinds LAN-only? (resolved: Operator 2026-10-24: (a) accepted; xteink.culture.dev is not a publicly accessible endpoint, so syncing transcript books through it is fine)

## Open parks

- [unknown_nonblocking] whether the operator's Xteink device is a mirror device (deletes propagate) - decides whether removing a bad book from the library also removes it from the device
