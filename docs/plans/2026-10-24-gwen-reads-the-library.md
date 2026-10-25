# Build Plan — Gwen reads the library

slug: `gwen-reads-the-library` · status: `exported` · from frame: `gwen-reads-the-library`

> Gwen learns only from the library: culture-rules ships the generic code to put transcripts, newspapers and to-dos into culture-library as items that are kept current under the same id, and the Gwen-specific flows are configured live

## Tasks

### t1 — library.push uploads any format the actor lists and replaces an item by id

- instruction: Only `culture_rules`/node/actions/library.py and tests/node/`test_library_push.py`. Do not touch `action_kinds.py`, runner.py or the editor (t7 does). Formats: actor param formats (list of lowercase words), step param format; map md->text/markdown, txt->text/plain, json->application/json, epub->application/epub+zip, pdf->application/pdf; filename is the title slug plus `.<format>`. Replace: actor `replace_path` (template with {`item_id`}) and `replace_method` (no default: require it explicitly), step param `item_id` (positive int). Reuse `_path` slot logic generalised to a slot name. Cite culture-library#10 in the docstring as the route owner.
- covers: c25, h15, c26, h16, c15, h9
- acceptance:
  - tests/node/`test_library_push.py`: format md sends a .md filename and text/markdown; a format the actor's formats does not list fails `invalid_request` before any request; no format sends EPUB exactly as before
  - tests/node/`test_library_push.py`: `item_id` sends the bytes to the actor's `replace_path` with {`item_id`} filled in, using `replace_method`; `item_id` without a `replace_path` fails `invalid_actor` before any request; the output carries the same `item_id`
  - `replace_path` is checked like `queue_path` (plain segments, {`item_id`} as exactly one whole segment); nothing about a library is built in

### t2 — llm.complete gains the locked recording brief and an answer cache beside its content file

- instruction: Only `culture_rules`/node/actions/llm.py (plus a small new module for the recording answer parser if cleaner) and tests/node/`test_llm_complete.py`. Follow `transcript_render.py`'s book cache (d4) for the atomic write and the sha256 key; cache only a parsed, valid answer. No catalog edits (t7).
- covers: c23, h13, c29, h19
- acceptance:
  - tests/node/`test_llm_complete.py`: brief recording sends the `RECORDING_BRIEF` constant; a step cannot replace it; the transcript brief and newspaper brief are unchanged
  - tests/node/`test_llm_complete.py`: with cache true and `content_path`, the first completed answer is stored atomically beside the content file keyed by the content file's sha256 and the brief, and a second run with a different model answer returns the first answer without calling the model
  - a parse helper validates the recording answer as JSON {summary: 1-2 lines, word, facts: list of short strings, `action_items`: list of short strings} and reports a refusal code otherwise

### t3 — transcript.render can write the transcript as Markdown with summary and key facts

- instruction: Only `culture_rules`/node/actions/`transcript_render.py` and its tests. Output the markdown as `content_base64` (UTF-8) plus format md so library.push can upload it; keep the EPUB cache keyed as today and add a separate .md cache only if needed for byte stability.
- covers: c17, h11
- acceptance:
  - tests/node/`test_transcript_render.py`: format md returns markdown text: the dated title as H1, the summary, a Key facts list, then a Transcript section with one paragraph per non-empty segment; the EPUB path is unchanged
  - a Hebrew or unlabelled transcript's Markdown is wrapped so it reads right to left (a dir=rtl block), an en one is not
  - the summary and facts come from the recording answer (t2's shape) or the old {summary, word}; a missing answer renders Summary unavailable and no facts

### t4 — checklist builtins: merge, set, render and bind over a stored list

- instruction: New module `culture_rules`/node/actions/checklist.py with four builtin ports over a checklists collection (one document per list key, rev compare-and-set via store.`update_if`, like `newspaper_issue.py`). ids are `<prefix>.<list key>.<n>` with the prefix a step param. Normalize text: casefold, collapse whitespace, strip punctuation at the ends. Do not register in runner.py (t7).
- covers: c24, h14, c16, h10, c27, h17, c28, h18
- acceptance:
  - tests/node/`test_checklist.py`: merge keeps the id and done mark of an item whose normalized text matches; a new item gets the next unused index (never reused, even after an item is removed upstream); an existing item not found again is kept
  - tests/node/`test_checklist.py`: set applies done/not done with at and by only when at is newer than the item's last applied at; an unknown id or list is ignored with a reason; two sets on different items in either order both survive
  - tests/node/`test_checklist.py`: render writes '- \[ \] text <!-- todo:<id> -->' and '- \[x\] text <!-- todo:<id> `done_at`=<iso> by=<by> -->' under a title, returns the list revision and an empty marker when the list has no items
  - tests/node/`test_checklist.py`: bind stores `item_id` and `pushed_revision` only if newer, and reports stale when the list's revision is ahead of what was pushed

### t5 — json.read builtin reads named fields from a JSON file under a root

- instruction: New module `culture_rules`/node/actions/`json_read.py` (builtin port) and its test. Reuse the root check in `transcript_file.py` / audio paths (resolve, then `is_relative_to`). Fields param: mapping output name -> dotted path. No registration (t7).
- covers: c34, h21
- acceptance:
  - tests/node/`test_json_read.py`: returns the named dotted fields as scalars, null for a missing field, and refuses a non-scalar value
  - tests/node/`test_json_read.py`: a path outside the root, a symlink pointing out of it, a missing file and a non-JSON file each fail with a final code before reading further

### t6 — audio.transcribe hashes the audio itself and fails a missing file finally

- instruction: Only `culture_rules`/node/actions/`audio_transcribe.py` (or the module that owns audio.transcribe) and its tests. Stream the hash in chunks. Output `audio_sha256` always, so downstream mappings work from enriched events.
- covers: c18, h12, c30, h20
- acceptance:
  - tests/node/`test_audio_transcribe.py`: without `audio_sha256` the port computes the file's sha256 and writes under it, giving the same result path as when the hash is given
  - tests/node/`test_audio_transcribe.py`: a missing audio file fails with a non-retryable code naming the missing audio; a given `audio_sha256` that does not match is still refused as today

### t7 — wire the new params and builtins: runner registration, action catalog, describe, editor

- instruction: Owns `culture_rules`/node/runner.py, `culture_rules`/model/`action_kinds.py`, `culture_rules`/model/describe.py, web/src/rules/ActionPicker.tsx and their tests. Regenerate the describe e2e fixture if it changes.
- depends on: t1, t2, t3, t4, t5, t6
- covers: c19, h2
- acceptance:
  - runner.py registers checklist.merge, checklist.set, checklist.render, checklist.bind and json.read; a workflow using each validates and runs on a MemoryStore node
  - `action_kinds.py`: library.push gains format and `item_id`, llm.complete's brief lists recording and gains cache, transcript.render gains format, audio.transcribe's `audio_sha256` becomes optional; tests/model/`test_action_kinds.py` pins them
  - the editor offers library.push formats from the actor (optionsFrom formats), the recording brief and the md format; vitest and tsc pass; describe glosses name the new builtins
  - grep of the diff finds no gwen, embodiment or recording-specific id outside docs examples

### t8 — docs, harness files, changelog and the version bump

- instruction: Docs describe what is on disk; mark the live Gwen flows as configured, not shipped.
- depends on: t7
- acceptance:
  - docs/actors/library.md documents formats and the replace mode (culture-library#10); a new docs/actors/checklist.md (or an operations page) documents checklist.\* and json.read with one worked configuration example that names no person
  - CLAUDE.md, QWEN.md, AGENTS.override.md and AGENTS.colleague.md describe the new pieces in step; markdownlint, teken doctor --strict and harness-smoke pass
  - minor version bump with a CHANGELOG entry; the full suite, vitest and the lint gates pass once

### t9 — after merge and rollout: configure the Gwen flows live and validate them

- instruction: Not a workforce task: the operator approves each live change; configuration is never committed (it names Gwen). Mint a library key that can also write todo once culture-library#10 ships.
- depends on: t8
- covers: c1, h1, c2, h6, c14, h7, c20, h3, c21, h4, c22, h5
- acceptance:
  - a live rule on pocketsync.recording.enriched (change created) reads meta.json via json.read, transcribes, and pushes a Markdown transcript item and, when there are action items, a todo item; one new recording shows both with no manual step
  - an embodiment.bus.todo event changes its line within 60 s and the todo item keeps its id (needs culture-library#10 live); a re-extraction keeps every id and done mark it still finds
  - the read-only scan again finds no embodiment memory ingest; the live newspaper push still sends kind newspaper with links

## Risks

- [unknown_nonblocking] culture-library#10's replace route and todo kind are on culture-library's side; t9's flip and todo items wait for them; our replace mode is configured, so the PR does not (task t9)
- [follow_up] embodiment must switch its To-dos page to read the library and carry the checklist comment id in its todo event (c31); until then flips use ids the stored lists don't know and are ignored (task t9)
