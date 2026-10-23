# Build Plan — Transcripts on the reader

slug: `transcripts-on-the-reader` · status: `exported` · from frame: `transcripts-on-the-reader`

> Every pocketsync recording now arrives on the Xteink reader as its own book, titled with its date and a short word, opening with a one- or two-line summary in the recording's language and then the transcript, right to left for Hebrew; past recordings are there too

## Tasks

### t1 — transcript.render built-in: a stdlib EPUB of one transcript with its summary

- instruction: Model the module on `culture_rules`/node/actions/`newspaper_render.py`'s EPUB plumbing (container, opf, nav, ncx, zip writing) but do not import from it or change it; write a fresh small module. Read the transcript with the schema in `culture_rules`/node/actions/`transcript_file.py`. Use zoneinfo for Asia/Jerusalem. Segments render as paragraphs in order; skip empty texts. This task owns all edits to `culture_rules`/model/`action_kinds.py` and `culture_rules`/node/runner.py in wave 1.
- covers: c4, h4, c7, h7
- acceptance:
  - `culture_rules`/node/actions/`transcript_render.py` renders a schema-1 transcript plus a summary reply into an EPUB; rendering the same inputs twice gives byte-identical bytes (fixed zip times and order)
  - a he or unlabelled-but-zipformer book has lang and xml:lang he, dir rtl and page-progression-direction rtl; an en book is ltr
  - the title is `<YYYY-MM-DD> <word>` with the date the Asia/Jerusalem local date of `recorded_at` (2026-10-23T22:30:00Z gives 2026-10-24); a missing or invalid `recorded_at` fails `invalid_request`
  - the summary reply must be JSON {summary, word}: summary 1-2 non-blank lines, word one short Latin word (^\[A-Za-z\]\[A-Za-z-\]{1,23}$, lowercased); anything else (or no reply) renders `<date> recording` and 'Summary unavailable.'
  - a `result_path` outside the actor's or step's transcripts root (symlinks resolved) fails `path_refused` before reading
  - outputs are scalars and base64 only: `content_base64`, title, date, word, `summary_used` (bool), `segment_count`; never the summary or transcript text
  - registered in `culture_rules`/node/runner.py and `culture_rules`/model/`action_kinds.py`; tests in tests/node/`test_transcript_render.py`

### t2 — llm.complete: a second locked brief, chosen by params.brief

- instruction: Edit only `culture_rules`/node/actions/llm.py (docstring + code) and tests/node/`test_llm_complete.py` (or the existing llm test file). Do not touch `culture_rules`/model/`action_kinds.py` (t1 owns it in this wave); if a param list there must change, report it instead.
- covers: c5, h5
- acceptance:
  - params.brief absent or 'newspaper' sends `NEWSPAPER_BRIEF` exactly as today (existing tests unchanged and green)
  - params.brief 'transcript' sends `TRANSCRIPT_BRIEF`: a fixed constant asking for JSON {"summary": 1-2 lines in the transcript's language, "word": one short Latin-letter word naming the topic}; no step text reaches the system message
  - any other brief value fails `invalid_request` before any request
  - content longer than `DEFAULT_MAX_CONTENT_CHARS` is still refused as today (the workflow passes at most the first 48000 characters)

### t3 — library.push: optional kind (book, article, transcript)

- instruction: Edit only `culture_rules`/node/actions/library.py and its test file. The live xteink accepts only book|article until agentculture/xteink ships transcript, so the bundle (t4) will pass article for now; kind transcript must still be accepted here.
- covers: c8, h8
- acceptance:
  - without params.kind the upload sends kind=article exactly as today (newspaper tests unchanged and green)
  - kind book, article or transcript is sent as given; any other value fails `invalid_request` before any request

### t4 — Bundle: transcript-to-reader workflow and its rule in docs/rules/pocket-transcribe, `recorded_at` passed through

- instruction: Follow docs/rules/pocket-transcribe and docs/rules/newspaper for shape; reuse the existing actors (cortex-spark2, newspaper-library, stt-he) - no new task-named actor. Workflow and step descriptions are left for the operator to write: copy the closest existing text as the fixture does for transcribe-file-he and flag it, never write AI prose for them. Use a concurrency key reader:{trigger.data.outputs.`audio_sha256`}. If the condition language cannot express a needed test (e.g. comparing two fields), move that branch into a workflow step and say so.
- depends on: t1, t2, t3
- covers: c1, h1, c2, h2, c3, h3, c6, h19, c11, h11, c12, h12, c22, h18
- acceptance:
  - rule pocket-to-reader (event trigger rules.run.succeeded, placement machine spark, ships disabled) starts workflow transcript-to-reader for transcribe-file-he successes, and for transcribe-file successes only when outputs.language is en; a whisper success for an unlabelled or he recording starts nothing; failed/cancelled runs start nothing (q3 b)
  - transcript-to-reader steps, all generic and config-specialised: for an en recording whose whisper windows were all empty, audio.transcribe on stt-he first (q1); then a code step reads at most the first 48000 transcript characters; llm.complete on cortex-spark2 with brief transcript and config.`on_error` skip; transcript.render; library.push (kind article until xteink ships transcript) on the existing xteink service actor
  - transcribe-file and transcribe-file-he take an optional `recorded_at` input and export it; both pocket-transcribe rules map it from trigger.data.`recording_at`
  - tests/node/`test_transcript_reader_flow.py` drives the chain on MemoryStore with stub ports: he success -> one book; en success -> one book; unlabelled whisper success -> none; failed zipformer -> none; summary failure -> book titled `<date> recording`; the same run twice -> identical bytes pushed; run and step outputs hold no transcript or summary text
  - the committed corpus counts and fixtures move from 11 to 12 workflows (tests/fixtures/`workflow_descriptions.json`, `fold_docs_rules.json`, condition snapshot by inserting lines only, describe-paragraph tests, web/e2e/workflows-described.spec.ts and its regenerated fixture); workflow names contain no parentheses

### t5 — Docs, harness files and version for transcripts on the reader

- instruction: Docs describe what is on disk; the transcript kind and the /xteink/transcripts/ folder are marked planned until agentculture/xteink and xteink-firmware ship. No speech text anywhere.
- depends on: t4
- covers: c17, h13, c18, h14
- acceptance:
  - docs/operations/transcripts-on-the-reader.md covers what it does, setup (actors, variables, enabling the rule), the backfill command, reading on the device, and removing a bad book; markdownlint clean
  - docs/operations/pocket-transcribe.md and docs/actors/stt.md link to it; CLAUDE.md, AGENTS.override.md, AGENTS.colleague.md and QWEN.md gain the same short bullet (four harness files in step); explain catalog/learn mention transcript.render if verbs or kinds are listed there
  - version bumped (minor) in pyproject.toml, uv.lock, CHANGELOG.md and docs/rules/pr-fixer/release.json; full suite, lint, teken doctor and harness-smoke green

### t6 — Cross-repo asks: xteink kind transcript, firmware folder per kind, `recording_at` on pocketsync#12

- instruction: Main agent, through the communicate skill's scripts.
- covers: c9, h9
- acceptance:
  - an issue on agentculture/xteink asks POST /api/library to accept kind transcript (quoting today's enum book|article) and to carry kind on the device queue item
  - an issue on agentculture/xteink-firmware asks placeFile to put a non-book kind under `/xteink/<kind>s/` (quoting kBooksDir and bookFileName), keeping the manifest and delete rules
  - a comment on agentculture/pocketsync#12 adds `recording_at` (UTC ISO-8601) to the landed event's data; all three are self-contained, signed by the script, counts only

### t7 — Live: release, enable the reader rule, backfill 33 recordings, verify on the device

- instruction: Ask before any rollout step that needs the operator; inject secrets, never read them. The STT rules stay disabled (c13). Report counts only.
- depends on: t5, t6
- covers: c10, h10, c19, h15, c20, h16, c21, h17
- acceptance:
  - after the PR merges, the release rolls out through culture-rules rollout (each step checked), the bundle is imported and pocket-to-reader enabled
  - the backfill runs transcript-to-reader once per recording, oldest first by meta.json `recording_at`, with the inputs from the existing transcript files; every run succeeds
  - xteink lists 33 dated items for 33 recordings; a second backfill adds 0 (created: false); the operator syncs the device and confirms the books read right (Hebrew right to left) and the names and summaries identify the recordings
