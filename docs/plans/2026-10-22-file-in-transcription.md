# Build Plan — File-in transcription

slug: `file-in-transcription` · status: `exported` · from frame: `file-in-transcription`

> culture-rules transcribes a given audio file: an audio.transcribe action on an agent actor sends the file to an OpenAI-compatible speech-to-text endpoint (the lobes gateway), splits long audio, and a disabled transcribe-file bundle, fired by pocketsync.recording.landed, writes a timestamped transcript file and exports scalar outputs for whoever reads it next

## Tasks

### t1 — Audio split module: ffprobe duration, silence-aware windows of at most 480 s, WAV window extraction

- instruction: New stdlib module `culture_rules`/node/actions/`audio_split.py`, tests in tests/node/`test_audio_split.py`. Functions: `require_tools`() -> raises a ToolMissing naming ffmpeg or ffprobe (shutil.which); `probe_duration`(path) via ffprobe; `plan_windows`(duration, silences, `max_s`=480, `search_s`=60) pure function returning \[(start, end)\]; `detect_silences`(path) via ffmpeg silencedetect parsing stderr; `extract_window`(path, start, end, dest) writing 16 kHz mono PCM16 WAV. subprocess argv lists only, timeouts, '# nosec B603'. Tests generate audio with ffmpeg (sine + silence gaps, lavfi) in `tmp_path` and skip with a reason when ffmpeg is absent; `plan_windows` is tested without ffmpeg. Never commit audio.
- covers: c6, h11, c28, h20
- acceptance:
  - `plan_windows`(1500, silences) returns contiguous non-overlapping windows covering 0..1500 within 0.1 s, none longer than 480 s, cut at a silence inside the last 60 s of a window when one exists, else at 480 s
  - a 480 s window extracted as 16 kHz mono PCM16 WAV is under 16 MiB (asserted on the real file)
  - `require_tools`() with a PATH lacking ffmpeg raises naming ffmpeg; tests skip cleanly when ffmpeg is not installed

### t2 — Transcript result file: schema-1 builder, model slug, atomic write, existing-result read, per-window work dir

- instruction: New stdlib module `culture_rules`/node/actions/`transcript_file.py`, tests in tests/node/`test_transcript_file.py`. `model_slug`(model) replaces every char outside \[A-Za-z0-9.`_`-\] with '-'. `result_path`(`output_dir`, `audio_sha256`, model). `build_result`(`audio_sha256`, model, language, duration, windows) where windows is a list of (start, end, reply) and a reply is {text} or {text, segments\[\]}: plain text -> one segment spanning the window, segments offset by window start, empty text kept as '' segment; adds `window_count`, `empty_window_count`, schema 1, `created_at`. `write_atomic`(path, doc) via NamedTemporaryFile(dir=same dir) + os.replace, returns sha256 of the bytes written. `read_existing`(path) returns the doc or None. WorkDir(`output_dir`, `audio_sha256`, model): get(i)/put(i, reply) atomic per-window JSON, clear() removes the dir.
- covers: c8, h13, c32, h24, c29, h21
- acceptance:
  - `build_result` with one plain-text window at 480-960 s yields one segment {start 480, end 960}; a segments reply at window start 480 shifts each segment by 480; an empty reply yields a '' segment and `empty_window_count` 1
  - `write_atomic` leaves no partial transcript file when the write raises midway (temp file removed), and returns the sha256 of the file on disk
  - WorkDir put/get round-trips per window, survives a new WorkDir instance (resume), and clear() removes it

### t4 — Editor: audio.transcribe in the action picker and web types

- instruction: web/src/api/types.ts kind union + comment; web/src/rules/ActionPicker.tsx label/params table entry and an actorsFor case offering enabled agent actors whose params carry `base_url` (as llm.complete does); a vitest case beside ActionPicker.newspaper.test.tsx. Run npm run typecheck and vitest in web/.
- covers: c14, h18
- acceptance:
  - the picker lists audio.transcribe, offers only agent actors with `base_url` for it, and web typecheck plus vitest pass

### t5 — The audio.transcribe port: actor checks, path roots, hash checks, split, upload, resume, error codes

- instruction: New `culture_rules`/node/actions/stt.py modelled on node/actions/llm.py (LlmCompletePort): `check_stt_actor` (agent kind, `base_url` via llm.`_base_url_problem`, model, grant `api_key`, `audio_root`, `output_root`), `_Refused` `<code>: <detail>` errors, ReplayLedger, injectable transport reusing library.`encode_multipart` and a no-proxy no-redirect urllib opener; per-request timeout min(remaining deadline, 900 s). Order: validate `audio_sha256` regex -> roots (resolve strict, `is_relative_to`) -> `require_tools` -> stream-hash the audio (`audio_mismatch`) -> existing result no-op -> probe/plan/extract windows into a TemporaryDirectory -> for each window not in WorkDir: POST, parse, WorkDir.put -> `build_result`, `write_atomic`, WorkDir.clear -> completed outputs. Register 'action:audio.transcribe' in node/runner.py `default_ports` and update its docstring (the catalogue entry lands in t3, after this task; a port with no catalogue entry is harmless). tests/node/`test_audio_transcribe.py` calls the port directly with a fake transport that answers 413 for a body over 16 MiB or a WAV over 600 s.
- depends on: t1, t2
- covers: c2, h8, c3, h9, c4, h10, c7, h12, c9, h14, c10, h15, c26, h6, c29, h21, c31, h23, c33, h25
- acceptance:
  - a generated 1500 s file is sent as >= 4 windows each <= 480 s and <= 16 MiB, and every segment start equals its window start within 0.05 s; an unsplit upload would get 413 from the fake
  - a second run with the same audio and model makes 0 transport calls and returns the same outputs
  - a retryable 503 on window 3 of 4 fails `stt_unavailable` retryable; the retry sends only windows 3 and 4
  - path outside `audio_root`, a symlink escaping it, an `output_dir` outside `output_root` -> `path_refused`; a bad `audio_sha256` -> `invalid_request`; changed bytes -> `audio_mismatch`; ffmpeg missing -> `ffmpeg_missing`; garbage audio -> `audio_undecodable`; 400 from the endpoint -> `stt_rejected`; connection refused -> `stt_unavailable`; none of them calls the endpoint except the last two
  - tests/node/`test_default_ports.py` passes with the kind registered; the api key never appears in any output or error

### t3 — Catalogue the audio.transcribe kind: `ACTION_KINDS`, describe phrase, endpoint-actor rule

- instruction: `culture_rules`/model/`action_kinds.py`: add `_k`('audio.transcribe', ...) with ParamSpec actor/`audio_path`/`audio_sha256`/`output_dir` (string, required) and language/model (string, optional); update the module docstring list of kinds needing params.actor. `culture_rules`/model/describe.py `ACTION_WORDS`: an entry naming the file (e.g. ('`audio_path`', 'of {}')). server/`model_actors.py`:337 `needs_endpoint` and actors/`model_rule.py` `is_endpoint_actor`: treat audio.transcribe like llm.complete (frame assumption c15, confirmed). Tests: tests/model/`test_action_kinds.py`, tests/model/`test_describe.py`, the model-rule/takeover tests. This task runs after t5 so the port is already registered and tests/node/`test_default_ports.py` (every catalogued kind has a port) stays green on both sides of the merge.
- depends on: t5
- covers: c2, h8, c23, h3
- acceptance:
  - `ACTION_KINDS`\['audio.transcribe'\] has the six typed params with the right required flags, and validate refuses a step missing `audio_path`
  - describe renders an audio.transcribe step with its actor and file in one line
  - an enabled agent actor referenced by an audio.transcribe step counts as an endpoint actor for `model_taken` and actor takeover, with a test

### t6 — The pocket-transcribe bundle, the event-to-run-event flow test, actor guide and setup doc

- instruction: docs/rules/pocket-transcribe/: actors/ (an STT agent actor example with grant:`LOBES_GATEWAY_API_KEY`, `audio_root`/`output_root`, pool and `max_concurrency` 1 — no live values beyond localhost), workflows/transcribe-file.json (generic: inputs `audio_path`, `audio_sha256`, language, model, `output_dir`; one audio.transcribe step with `timeout_s` 14400; outputs `result_path`, `result_sha256`, `audio_sha256`, model, language, `segment_count`, `window_count`, `empty_window_count`; description plain, notes technical, no Pocket words), rules/pocket-transcribe.json (enabled false, event params.type pocketsync.recording.landed, `concurrency_key` stt:{trigger.data.`audio_sha256`}, inputs mapped from trigger.data, `output_dir` as a variable), variables.json + seed-variables.sh like newspaper. tests/rules/`test_pocket_transcribe_bundle.py` mirrors `test_newspaper_bundle.py`. tests/node/`test_transcribe_file_flow.py` drives a synthetic event through MemoryStore + executor + fake transport to rules.run.succeeded, and a direct `start_workflow` with another model. docs/actors/stt.md (llm.md format; the model must match lobes `STT_MODEL`) + a README row; docs/operations/pocket-transcribe.md naming pocketsync and the operator, the event fields, inputs, file shape and outputs. Regenerate tests/fixtures/`fold_docs_rules.json` / describe fixtures if their tests require it.
- depends on: t5, t3
- covers: c11, h16, c13, h17, c14, h18, c22, h2, c24, h4, c25, h5, c30, h22
- acceptance:
  - the bundle imports into a MemoryStore with 0 errors, the rule disabled, secrets scan clean, transcribe step `timeout_s` >= 14400, and the workflow names no Pocket field
  - a synthetic pocketsync.recording.landed event with the rule enabled ends in rules.run.succeeded whose data.outputs carry the eight outputs, and the file exists
  - a direct run with another model writes a second file and leaves the first byte-identical
  - docs/actors/stt.md and docs/operations/pocket-transcribe.md exist, lint clean, and the README table links stt.md

### t7 — Release 0.23.0: version bump, changelog, CLAUDE.md and harness files in step, scope check

- instruction: version-bump minor (pyproject.toml, CHANGELOG.md, uv.lock); add the audio.transcribe kind and pocket-transcribe bundle to CLAUDE.md, AGENTS.override.md, AGENTS.colleague.md and QWEN.md in step; run the full lint set and uv run pytest -n auto; grep the diff for MongoClient/pymongo and transcript.json reads; confirm no text.embed/segments.chunk/text.classify/text.translate/transcript.compare code landed.
- depends on: t4, t6
- covers: c1, h1, c18, h19
- acceptance:
  - version is 0.23.0 with a CHANGELOG entry; the four harness files mention the kind; scripts/harness-smoke.py --stage config passes
  - the full suite, black, isort, flake8, bandit, markdownlint, scan-secrets and teken doctor --strict pass
  - the diff adds no Mongo client outside the store and no items 4-9 code

### t8 — Live check after merge: roll out 0.23.0, enrol the STT actor, transcribe one long recording by a direct run

- instruction: Operator-gated. After merge: rollout via the CLI (each step checked); confirm with the operator the gateway's stt route works (park v1) and the actor's machine/model/pool (park v3); import the bundle (rule stays disabled); run workflows run transcribe-file on one landed mp3 over 600 s the operator picks; file evidence. Ask before any sparkrun run/stop.
- depends on: t7
- covers: c27, h7
- acceptance:
  - a direct run on a real recording over 600 s ends succeeded with `segment_count` >= 2, and the operator reads the Hebrew transcript as usable; evidence filed
