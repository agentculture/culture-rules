# File-in transcription

> culture-rules transcribes a given audio file: an audio.transcribe action on an agent actor sends the file to an OpenAI-compatible speech-to-text endpoint (the lobes gateway), splits long audio, and a disabled transcribe-file bundle, fired by pocketsync.recording.landed, writes a timestamped transcript file and exports scalar outputs for whoever reads it next
> instruction: build in `culture_rules`/node/actions/stt.py (port), `culture_rules`/node/actions/`audio_split.py` (ffprobe/ffmpeg windows), model/`action_kinds.py`, node/runner.py, docs/rules/pocket-transcribe/, docs/actors/stt.md, docs/operations/pocket-transcribe.md

## Audience

- pocketsync, which emits pocketsync.recording.landed and reads the result file back, and the operator, who enrols the STT actor, imports and enables the bundle; plus any later workflow that holds a path to an audio file
  - instruction: check docs/operations/pocket-transcribe.md names both readers and the hand-off contract pocketsync builds against

## Before → After

- Before: only Pocket's own transcript exists; pocketsync's README (:26) lists re-running STT as a non-goal, no culture-rules action can send audio to the lobes STT route, and nothing in `culture_rules` reads a rule-supplied file path
  - instruction: grep `culture_rules` for audio.transcribe and /v1/audio/transcriptions on main b3a3025: no hits
- After: with the bundle imported and its rule enabled, a pocketsync.recording.landed event makes a run on the STT actor's machine that writes `<output_dir>/<audio_sha256>/transcript.<model-slug>.json` and ends rules.run.succeeded with `result_path`, `result_sha256`, `audio_sha256`, model, language and `segment_count` in data.outputs; the same audio and model again is a no-op, and a different model writes a second file beside it
  - instruction: prove with an engine-level test: synthetic event into MemoryStore with a fake STT transport, assert the run event's outputs and the file on disk

## Why it matters

- our own Hebrew-capable, timestamped transcript unblocks pocketsync own-transcription, and putting it in a generic file-in workflow keeps processing out of pocketsync, which shrinks to pull, webhook, events and its own store (#73)
  - instruction: quote #73's summary in the spec's why section; the workflow inputs carry no Pocket-specific field

## Requirements

- audio.transcribe is a new action kind (`culture_rules`/model/`action_kinds.py` `_k` entry, a port in `culture_rules`/node/actions/, registered as action:audio.transcribe in node/runner.py `default_ports`) on an agent actor whose params mirror llm.complete: `base_url`, model, `api_key` grant:..., `concurrency_pool`, `max_concurrency`; validation reuses llm.py's `_base_url_problem` and grant resolve
  - instruction: copy LlmCompletePort's shape: `check_stt_actor`, ReplayLedger, injectable transport; register action:audio.transcribe in node/runner.py `default_ports`
  - honesty: audio.transcribe appears in `ACTION_KINDS` with typed params (actor, `audio_path`, `audio_sha256`, `output_dir`, language, model) and has a port registered under action:audio.transcribe, so `test_default_ports` passes
- audio.transcribe reads its audio file only under an allowlisted root set in the actor's params and refuses a path outside it or a symlink that resolves outside it (error code `path_refused`); nothing in `culture_rules` does this today, so the containment code is new (the only precedent is io/gitrepo.py:56-60, resolve() + `is_relative_to`)
  - instruction: actor params `audio_root` and `output_root`; resolve(strict=True) then `is_relative_to`(root.resolve()); test with `tmp_path` symlinks
  - honesty: a path outside the root, a '..' escape and a symlink inside the root pointing outside it are all refused with `path_refused` before any byte is read or sent; the `output_dir` is held to its own allowlisted root the same way
- the upload is a multipart POST to <`base_url`>/v1/audio/transcriptions with the fields file and language, built with the existing `encode_multipart` in node/actions/library.py:123-148 (stdlib only, no new runtime dependency)
  - instruction: reuse library.`encode_multipart`; build the URL like llm.`_completions_url` but for /v1/audio/transcriptions; a fake transport in tests asserts the body fields
  - honesty: the request is multipart with file and language fields to <`base_url`>/v1/audio/transcriptions with Authorization Bearer from the grant, no proxy, no redirects; the key never appears in errors or outputs
- long audio (over 600 s or 16 MiB) is split by ffmpeg on the runner host into windows under both caps, cut at silences where ffmpeg silencedetect finds one, each window sent separately, and every segment's start/end offset by its window's start; ffmpeg/ffprobe are system binaries called through subprocess argv lists (no shell, the nosec B603 convention), present on spark at /usr/bin
  - instruction: ffprobe for duration; ffmpeg silencedetect over the file, choose cuts <= 600 s apart and keep each window's encoded size under 16 MiB (re-encode windows to 16 kHz mono WAV, about 1.9 MiB/min); ffmpeg argv-only subprocess with a timeout
  - honesty: windows are cut at detected silences when one falls in the search span and at the hard limit otherwise; window boundaries never overlap or drop audio, so the windows' durations sum to the file's duration within 0.1 s
- the result file shape is {schema: 1, `audio_sha256`, model, language, duration, segments: \[{start, end, text}\], `created_at`}; while lobes returns only {text}, each window becomes one segment covering the window, and a later `verbose_json` response (lobes-cli#328) drops finer segments into the same list with no schema change
  - instruction: one dataclass/dict builder for the result; accept both {text} and {text, segments\[\]} replies
  - honesty: a plain {text} reply becomes exactly one segment {start: window start, end: window end, text}; a `verbose_json` reply with segments is offset and kept as-is; schema stays 1 in both cases
- the action writes `<output_dir>/<audio_sha256>/transcript.<model-slug>.json` atomically (temp file then rename) under an allowlisted output root, and returns only scalars (`result_path`, `result_sha256`, `audio_sha256`, model, language, `segment_count`) so the transcript never sits in run state or in the rules.run.succeeded event
  - instruction: tempfile.NamedTemporaryFile(dir=`target_dir`, delete=False) + os.replace; model slug = model id with every char outside \[A-Za-z0-9.`_`-\] replaced by '-'
  - honesty: the result file is written to a temp file in the same directory and renamed, so a killed run leaves no partial transcript.\*.json; the outputs carry `result_sha256` of the bytes on disk
- re-running the same (`audio_sha256`, model) is a cheap no-op: when the result file exists and its recorded `audio_sha256` and model match, the action returns the existing file's outputs without calling the endpoint
  - instruction: check before ffmpeg runs; hash the audio in streaming 1 MiB reads
  - honesty: the no-op check reads the existing file's `audio_sha256` and model fields and also verifies the input audio's sha256 matches `audio_sha256` before trusting either; a mismatch re-transcribes and replaces the file
- failures end the run failed with distinct error codes: `stt_unavailable` (retryable: connection error, 5xx, 408, 429), `stt_rejected` (other 4xx incl. 413), `audio_undecodable` (ffmpeg/ffprobe or the endpoint's 400 `invalid_audio`), `path_refused`, plus the shared `actor_kind_mismatch`/`secret_unresolved`/`stt_actor_invalid`
  - instruction: use the `_Refused` `'<code>: <detail>'` convention from llm.py; retryable only for `stt_unavailable`
  - honesty: each failure maps to exactly one code and only `stt_unavailable` is retryable; a run that fails ends in rules.run.failed with that code in `error_code`
- a transcribe-file workflow (inputs `audio_path`, `audio_sha256`, language optional, model optional, `output_dir`; exported outputs `result_path`, `result_sha256`, `audio_sha256`, model, language, `segment_count`) ships in a bundle docs/rules/pocket-transcribe/ with actors/, workflows/, rules/ and variables like docs/rules/newspaper, the rule disabled, triggered by event type pocketsync.recording.landed with concurrency key stt:{trigger.data.`audio_sha256`}, and a bundle test like tests/rules/`test_newspaper_bundle.py`
  - instruction: copy docs/rules/newspaper's layout; tests/rules/`test_pocket_transcribe_bundle.py` mirrors `test_newspaper_bundle.py`
  - honesty: the bundle imports cleanly into a MemoryStore with the rule disabled, passes the secrets scan, its workflow uses description and notes, and the rule's concurrency key is stt:{trigger.data.`audio_sha256`}
- re-transcribe with a newer model is a direct workflows run transcribe-file --input model=... (cli/`_commands`/workflows.py:31-73, `start_workflow` typed inputs); since the file name carries the model slug, older results stay beside it
  - instruction: no new code path: covered by a test calling `start_workflow` with model set
  - honesty: a direct run with model=X writes transcript.<slug(X)>.json and leaves an existing file for another model untouched
- the new kind is wired everywhere the catalogue reaches: model/describe.py `ACTION_WORDS`, web/src/rules/ActionPicker.tsx (label/params table and an actorsFor case), web/src/api/types.ts kind union, tests/node/`test_default_ports.py`, an actor guide docs/actors/stt.md with a row in docs/actors/README.md; api/openapi.json has no action-kind enum, and CLI/MCP parity follows `ACTION_KINDS` with no extra registration
  - instruction: update describe.py `ACTION_WORDS`, web/src/rules/ActionPicker.tsx (table + actorsFor), web/src/api/types.ts, docs/actors/README.md; extend server/`model_actors.py` `needs_endpoint` if c15 is confirmed
  - honesty: the editor's action picker offers audio.transcribe only with agent actors whose params carry `base_url`, and the web typecheck and vitest pass; docs/actors/stt.md exists with a README row
- windows are re-encoded to 16 kHz mono PCM16 WAV (32 000 bytes/s), so the 16 MiB upload cap binds at about 524 s before the 600 s cap does; windows are at most 480 s long, cut earlier at a silence when one falls in the last 60 s of the window
  - instruction: constant `MAX_WINDOW_S` = 480 in the split module, with a test asserting 480 s of 16 kHz mono PCM16 plus header stays under 16 MiB
  - honesty: no window sent in the tests is longer than 480 s or larger than 16 MiB, including the last one
- a retry resumes rather than restarts: each window's reply is kept in a work directory under the output root keyed by (`audio_sha256`, model), a retried attempt sends only the windows still missing, and the work directory is removed after the final rename
  - instruction: work dir `<output_root>/<audio_sha256>/.work.<model-slug>/window-NNNN.json`, each written atomically; test: fail window 3 of 5 with 503, retry, assert windows 1-2 are not re-sent
  - honesty: a test fails one window mid-file with a retryable error and the retry sends only that window and the ones after it
- the bundle's transcribe step sets `timeout_s` 14400 explicitly, because the engine default `DEFAULT_TIMEOUT_S` is 3600 s (engine/runs.py:306) and the longest landed recording is 3.8 h; with resume, a timed-out attempt continues on retry
  - instruction: set `timeout_s` on the step in docs/rules/pocket-transcribe/workflows/transcribe-file.json; the bundle test asserts it
  - honesty: the bundle test reads the transcribe step's `timeout_s` and asserts it is at least 14400
- `audio_sha256` must be 64 lowercase hex characters (`invalid_request` otherwise) before it forms any path, and the action hashes the audio file and refuses a mismatch with `audio_mismatch` before transcribing, not only on the no-op path
  - instruction: validate with a regex before building paths; streaming sha256; tests for '../x' as `audio_sha256` and for a file whose bytes changed after the event
  - honesty: a crafted `audio_sha256` such as '../../etc' never reaches a filesystem call, and a file whose hash differs from the event's is never sent to the endpoint
- silent gaps stay visible: a window whose reply text is empty is kept as a segment with empty text, and the result file carries `window_count` and `empty_window_count`, because lobes drops low-confidence 30 s chunks (`MIN_AVG_LOGPROB`) without saying so until lobes-cli#328's dropped\[\] ships
  - instruction: add `window_count` and `empty_window_count` to the result file (schema 1, additive) and to the exported outputs
  - honesty: a fake reply of empty text for one window yields a segment with text '' at that window's times and `empty_window_count` 1
- a missing ffmpeg or ffprobe on the actor's machine fails at once with `ffmpeg_missing` (not retryable), naming the binary, instead of a generic error
  - instruction: shutil.which on the node's PATH before any work; the node runs as a systemd --user unit, so test with a PATH lacking ffmpeg
  - honesty: a test with ffmpeg absent from PATH ends failed with `ffmpeg_missing` and no endpoint call

## Honesty conditions

- the announcement ships whole: the action kind, the long-audio split, the disabled bundle and the result file all exist in the PR, and none of #73 items 4-9 do
- no new code opens a Mongo connection outside culture-rules' own store, and no step reads Pocket's transcript.json
- pocketsync can build its side from the committed docs alone: the event name and data fields, the workflow inputs, the result-file shape and the six outputs are written down in one place
- on main b3a3025 there is no audio.transcribe kind and no code posting to /v1/audio/transcriptions
- transcribe-file has no input or step that names Pocket; only the bundled rule's trigger and input mapping do
- the after-state is proven by a test that drives a synthetic pocketsync.recording.landed event through the engine to a rules.run.succeeded event, not only by unit tests of the port
- the long-audio test really exceeds both caps' logic: the fake server refuses a window over 600 s or 16 MiB with 413, so an unsplit upload would fail the test
- the live check uses a direct run on a file the operator picks; it does not need pocketsync to emit events first

## Success signals

- in tests with a fake STT server and synthetic audio: a 1500 s file is sent as at least 3 windows each <= 600 s and <= 16 MiB, every segment's start is offset by its window start within 0.05 s; a second run of the same (`audio_sha256`, model) makes 0 endpoint calls; 4 failure cases (endpoint down, undecodable audio, path outside the root, symlink escaping the root) end failed with 4 distinct error codes
  - instruction: tests/node/`test_audio_transcribe.py` generates audio with ffmpeg (sine + silence gaps) in `tmp_path`; skip with a reason if ffmpeg is absent, never commit audio
- after rollout, one real recording longer than 600 s transcribes on spark through a direct workflows run transcribe-file with `segment_count` >= 2 and a Hebrew transcript the operator reads as usable
  - instruction: operator-side, post-merge, during /validate-delivery; needs the STT actor enrolled and the gateway's stt route confirmed (v1)

## Scope / boundaries

- culture-rules never writes pocketsync-mongo or any recordings store, and never reads Pocket's transcript as input to its own; it reads the landed audio file and writes only its own result file
  - instruction: review-time check: grep the diff for pymongo/MongoClient and transcript.json reads

## Non-goals

- this PR ships part A only: audio.transcribe and transcribe-file. text.embed, segments.chunk, text.classify, text.translate, transcript.compare and the chunk-embed-classify chain (#73 items 4-9) are later work, since the 2026-10-22 alignment comment says they have no store to write into outside pocketsync yet
- no distil-to-Gwen step in this PR: embodiment memory ingest/forget does not exist yet (only the frame .devague/frames/gwen-remembers-recordings.json in agentculture/embodiment, issue #94), so the distil workflow waits for that verb

## Assumptions

- the lobes Whisper sidecar (lobes-cli lobes/templates/fleet/`listen_server_whisper.py`:593-596) reads only file and language, ignores model and `response_format`, and returns {"text"} only (:469-470); it already transcodes m4a via ffmpeg and chunks internally at 30 s quiet frames, but caps one request at `STT_MAX_UPLOAD_BYTES` 16 MiB (413 `upload_too_large`) and `STT_MAX_AUDIO_SECONDS` 600 s (413 `clip_too_long`); language defaults to he
- pocketsync.recording.landed (3 segments) is ingested by the existing \*.\*.\* subscription under `DEFAULT_DEPTH` 4 (events/`events_cli_adapter.py`:58-66) and matched by params.type equality (engine/matching.py:196-216); pocketsync does not emit it yet (pocketsync/events.py emits only .enriched and .reclassified), so tests use a synthetic event
- an STT agent actor counts as a model endpoint actor for the `model_taken` rule and actors references/repoint (actors/`model_rule.py` `is_endpoint_actor`, server/`model_actors.py`:337 `needs_endpoint` = 'llm.complete' in kinds), so `needs_endpoint` grows to include audio.transcribe
- the model recorded in the file name and result is the STT actor's declared model; lobes ignores the model form field (`listen_server_whisper.py`:595-596) and does not report which model served, so if lobes' `STT_MODEL` changes under the actor the label is wrong until the actor is updated
  - instruction: say so in docs/actors/stt.md: the actor's model must match the gateway's `STT_MODEL`; the operator checks it at enrolment
- landed Pocket audio is mp3 (all 32 audio files under ~/.local/share/pocketsync/files on 2026-10-23), not the m4a the issue names; ffmpeg decodes both, and tests cover mp3 and wav input
  - instruction: generate an mp3 fixture with ffmpeg in the test (libmp3lame), skip if the encoder is unavailable

## Scope exploration

- `s1` — `culture_rules/node/actions/llm.py + model/action_kinds.py + node/runner.py`: llm.complete is the template: `check_llm_actor` validates `base_url`/model/grant `api_key`, urllib transport with no proxy and no redirects, typed `_Refused` error codes (`llm_unavailable` retryable on OSError/5xx/408/429), ReplayLedger idempotency, `MAX_TIMEOUT` 600 s cap at llm.py:108; kinds dispatch by `action:<kind>` in `default_ports` (runner.py:343)
  - seeds: `c2`
- `s2` — `culture_rules (path containment search)`: no allowlisted-root, `O_NOFOLLOW`, `is_symlink` or realpath logic exists for user-named files; library.push takes `content_base64` rather than a path; io/gitrepo.py:56-60 `_subdir` is the only resolve()+`is_relative_to` precedent
  - seeds: `c3`
- `s3` — `culture_rules/node/actions/library.py`: `encode_multipart`(fields, files) at :123-148 with a checked random boundary already exists and can be reused for the transcription upload; library.py:187-200 has its own no-redirect urllib transport
  - seeds: `c4`
- `s4` — `lobes-cli listen_server_whisper.py + lobes/roles.py`: STT route /v1/audio/transcriptions (roles.py:225), Hebrew via ivrit-ai/whisper-large-v3-turbo in docker-compose.audio-he.yml; no `verbose_json` anywhere and no branch for lobes-cli#328; local checkout is on scope/outsource-experimentation, not main, and was not fetched
  - seeds: `c5`
- `s5` — `spark host + culture_rules subprocess conventions`: ffmpeg and ffprobe exist at /usr/bin on spark; `culture_rules` has no ffmpeg use yet; subprocess is used argv-only with nosec B603 in actors/secrets.py, actors/code.py:172, node/actions/`github_pr.py`:225
  - seeds: `c6`
- `s6` — `issue #73 lobes status comment (2026-10-22)`: lobes-cli#303 merged as 0.85.0 (m4a decode, 30 s chunking); timestamps requested in lobes-cli#328 as `response_format`=`verbose_json` with segments and dropped\[\]; build against `verbose_json` and treat plain {text} as one segment
  - seeds: `c7`
- `s7` — `docs/run-events.md + engine/run_completions.py:84-161`: rules.run.succeeded/failed carry data.outputs = the workflow's declared exported outputs plus `error_code`/`error_message`; only `SUBJECT_FIELDS` (PR fields) are lifted to top level, so `audio_sha256` and `recording_id` reach consumers only as data.outputs.\*; no per-output size limit beyond the ingest quarantine caps (8 KiB / 16 KiB)
  - seeds: `c8`
- `s8` — `culture_rules engine concurrency + idempotency (model/rule.py:89-120, model/action.py:29, engine/runs.py:2229-2292)`: Rule.`concurrency_key` with `{trigger.<path>}` placeholders dedupes firings globally, so stt:{trigger.data.`audio_sha256`} is valid; step config.when can skip and config.`on_error` skip exists; the no-op check fits inside the port itself, no new step kind needed
  - seeds: `c9`
- `s9` — `culture_rules/node/actions/llm.py error codes`: llm.complete's `_Refused` `'<code>: <detail>'` convention and retryable flag (executor retries, the port does not loop) is the model for the `stt_`\* codes
  - seeds: `c10`
- `s10` — `docs/rules/newspaper + tests/rules/test_newspaper_bundle.py + docs/operations/newspaper.md`: bundle = one JSON per entity in actors/ workflows/ rules/ plus variables.json and seed-variables.sh; only the rule ships enabled:false; test imports into MemoryStore, scans secrets, runs the seed script dry; committed workflows must use notes (`test_committed_notes_migrated.py`)
  - seeds: `c11`
- `s11` — `culture_rules/events/events_cli_adapter.py + pocketsync/pocketsync/events.py`: depth patterns \* .. \*.\*.\*.\* cover 3-segment types; trigger maps trigger.data.\* to workflow inputs through WorkflowRef.inputs refs/$literal/$var (model/rule.py:40-50); pocketsync emits no landed event today
  - seeds: `c12`
- `s12` — `culture_rules/cli/_commands/workflows.py + engine/runs.py:890-902`: workflows run takes --input name=value / --inputs-json, validated against typed input ports (422 `invalid_inputs`); direct runs have null trigger fields and no concurrency key
  - seeds: `c13`
- `s13` — `action-kind registration sweep (describe.py, ActionPicker.tsx, types.ts, openapi.json, tests)`: registration sites: `action_kinds.py` `_KINDS`, describe.py:92-107, runner.py `default_ports`, ActionPicker.tsx :95-118 and actorsFor :213-232, types.ts :102-111, `test_default_ports.py`:31-36; openapi.json has no kind enum; explain/catalog.py describes kinds generically
  - seeds: `c14`
- `s14` — `culture_rules/actors/model_rule.py + server/model_actors.py:337`: the 0.22.0 one-enabled-endpoint-per-model rule and takeover check key on llm.complete only; an STT lane actor (one actor per model lane) needs the same treatment
  - seeds: `c15`
- `s15` — `issue #73 comments 2026-10-22 (Gwen alignment)`: items 4-6 and 9 are explicitly deferred until after part A and distil-to-Gwen; the recordings store stays in pocketsync for phase 1; land-recording/culture-chat ingest is dropped
  - seeds: `c16`
- `s16` — `/home/spark/git/embodiment (HEAD 4514bb8)`: no memory ingest verb in code; only a devague frame describes ingest --file ndjson / forget --source pocket; machine.command can pin argv (actors/code.py `bind_argv`, undeclared args refused) and step config.action.idempotent clears `unsafe_retry` when the verb lands
  - seeds: `c17`
- `s17` — `issue #73 body + pocketsync README :26`: pocketsync stays the only writer of pocketsync-mongo; Pocket's transcript is untouched; consumers read `result_path` back
  - seeds: `c18`
- `s18` — `live lobes gateway http://127.0.0.1:8001 (probe, no audio sent)`: unauthenticated GET /v1/models returned an empty list and unauthenticated POST /v1/audio/transcriptions returned 401; the gateway is up and keyed, the stt route's availability needs the gateway key
- `s19` — `challenge pass / failure-mode lens: lobes caps vs WAV windows`: 600 s of 16 kHz mono PCM16 is 19.2 MB (18.3 MiB), over `STT_MAX_UPLOAD_BYTES` 16 MiB (`listen_server_whisper.py`:291); the binding cap is size, not duration
  - seeds: `c28`
- `s20` — `challenge pass / measurement probe: ffprobe over ~/.local/share/pocketsync/files/*.mp3 (2026-10-23)`: 32 recordings carry audio, all mp3; 3 are over 600 s and 2 over 16 MiB; the longest is 13 704 s (3.8 h, about 29 windows of 480 s), the mean 805 s; a retry that restarts would re-send hours of audio to the shared GPU
  - seeds: `c29`
- `s21` — `challenge pass / operations lens: engine/runs.py:50-85, 306-312`: every dispatch gets deadline = now + `timeout_s`, default 3600 s, and may queue blocked up to 2x that (`QUEUE_LIMIT_FACTOR` 2.0); Step.`timeout_s` (model/workflow.py:99) overrides it
  - seeds: `c30`
- `s22` — `challenge pass / security lens: event data as path input`: `audio_path` and `audio_sha256` arrive from any publisher on the events bus (\*.\*.\* subscriptions, `events_cli_adapter.py`:62-66); `audio_sha256` becomes a directory name in the output layout, so it is a path-injection surface unless validated
  - seeds: `c31`
- `s23` — `challenge pass / observability lens: lobes listen_server_whisper.py per-window drop`: chunks under `MIN_AVG_LOGPROB` are dropped server-side and `join_chunk_texts` concatenates the rest, so a window can come back shorter or empty with no signal
  - seeds: `c32`
- `s24` — `challenge pass / adjacent-systems lens: node service and host tools`: culture-rules-node.service and pocketsync-serve.service both run as the spark user (systemctl --user), so the node can read ~/.local/share/pocketsync/files (drwxrwxr-x spark); ffmpeg/ffprobe are in /usr/bin on spark only as far as checked
  - seeds: `c33`
- `s25` — `challenge pass / unstated-assumptions lens: lobes model selection`: lobes picks the STT model from `STT_MODEL` env (roles.py:285-298, compose audio-he :80 ivrit-ai/whisper-large-v3-turbo); the request's model is not read
  - seeds: `c34`
- `s26` — `challenge pass / counter-evidence: file types under the pocketsync files root`: find over the files root: 66 json, 66 md, 32 mp3, 1 jsonl, no m4a and no symlinks
  - seeds: `c35`
- `s27` — `challenge pass / placement lens: engine/runs.py:1380-1407 _placement and _action_placement`: clean: a built-in action step with no placement runs on its actor's machine (Placement(actor=...)), and resumed dispatches read the same placement, so the file read happens on the STT actor's host without new placement code
- `s28` — `challenge pass / concurrency lens: concurrency key + os.replace`: clean for rule firings (stt:{trigger.data.`audio_sha256`} dedupes per audio); two direct runs of the same audio and model race only on os.replace of identical content and a shared work dir, residual risk small — window files are written atomically and re-sendable
- `s29` — `challenge pass / reversibility and rollback lens`: clean: the kind is inert until a workflow uses it and the bundle rule ships disabled; rollback is disabling the rule; result files are additive and model-suffixed, nothing is overwritten except a hash-mismatched stale file (c9)

## Decisions

- audio.transcribe is an action kind, not a machine.command on a runner (operator, 2026-10-23, #73 open question 2)
- long audio is split in culture-rules with ffmpeg on the runner host, not in lobes (operator, 2026-10-23, #73 open question 3)
- this PR ships part A only (audio.transcribe and the transcribe-file bundle, #73 items 1-3); items 4-9 and distil-to-Gwen are follow-ups (operator, 2026-10-23)

## Hard questions

- \#73 open question 2: is audio.transcribe an action kind (pools, limits, editor picker, ReplayLedger idempotency, typed error codes) or a machine.command on a runner (quicker, but `supports_idempotency_key` False → `unsafe_retry`, 60 s default timeout, stdout as raw text)? Recommendation: action kind (resolved: operator 2026-10-23: audio.transcribe is an action kind)
- \#73 open question 3: is long audio split here (ffmpeg on the runner host, timestamp offsets) or in lobes (raise `STT_MAX_`\* or split server-side, asked in lobes-cli#328)? Recommendation: here, per the lobes status comment, until lobes answers (resolved: operator 2026-10-23: split here, ffmpeg on the runner host)
- PR scope: part A only (audio.transcribe + transcribe-file bundle, items 1-3), with embed/chunk/classify/translate/compare and distil-to-Gwen as follow-ups — or also some of items 4-8 in this PR? (resolved: operator 2026-10-23: part A only)

## Open parks

- [unknown_nonblocking] whether the live lobes gateway at 127.0.0.1:8001 serves the stt role today: unauthenticated POST /v1/audio/transcriptions returns 401 and /v1/models lists nothing without the key; checking needs `LOBES_GATEWAY_API_KEY` and is an operator-side probe at rollout, not in this PR
- [unknown_nonblocking] which machine, model id and concurrency pool the STT lane actor uses (spark, ivrit-ai/whisper-large-v3-turbo, a pool separate from qwen-spark2?) — the STT sidecar is a separate GPU process, so sharing a pool with the LLM lanes is a choice
- [unknown_nonblocking] whether STT and the qwen-spark2 LLM contend for the same GPU memory on spark when both run (sidecar placement is a lobes/sparkrun matter); `max_concurrency` 1 on the STT actor bounds culture-rules' side only
- [out_of_scope] pocketsync.recording.enriched/.reclassified compatibility (an event.publish action) is undecided in #73 but pocketsync keeps emitting them while it still enriches, so it does not touch part A
