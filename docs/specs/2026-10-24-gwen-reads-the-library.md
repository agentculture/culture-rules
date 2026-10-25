# Gwen reads the library

> Gwen learns only from the library: culture-rules ships the generic code to put transcripts, newspapers and to-dos into culture-library as items that are kept current under the same id, and the Gwen-specific flows are configured live
> instruction: check the live configuration after the PR: every Gwen-facing item is a culture-library item made by generic steps

## Audience

- the operator, who configures the Gwen flows live from generic steps, and Gwen (embodiment), who reads culture-library with GET only and is never a writer
  - instruction: grep the diff for gwen, embodiment and recording-specific ids: none outside docs examples

## Before → After

- Before: Gwen has two inputs that drift: the library holds 33 transcript books as EPUB `document` items and the newspapers, there are no to-dos in the library at all, transcription never fires because it waits for an event pocketsync never sends, and culture-rules can only upload new EPUB items, never change one
  - instruction: keep the scope entries s1-s13 as the record of the before state
- After: each new Pocket recording is transcribed on its own, and culture-library gets a Markdown `transcript` item (summary and key facts, then the transcript) and, when the recording has action items, a `todo` checklist item; when Gwen marks a to-do done, the checklist item is replaced under the same id; everything Gwen knows is in the library
  - instruction: after configuration, run one recording end to end and one todo flip on the live system and record the evidence

## Requirements

- culture-rules asks culture-library (an issue on its repo) for a replace-under-the-same-id route and a `todo` kind; library.push gains an actor-configured replace path that uses the route (operator 2026-10-25)
  - instruction: file the issue with the communicate skill on agentculture/culture-library; link it from the spec and the PR
  - honesty: an issue on agentculture/culture-library asks for the replace route and the todo kind, and library.push's replace mode is driven only by the actor's `replace_path` and `replace_method`
- a recording's to-dos are extracted by a model through a new locked brief over our transcript, not taken from Pocket's action items (operator 2026-10-25); because model output can vary between runs, re-extraction must still keep existing ids and done marks
  - instruction: tests/node for checklist.merge: two extractions with reordered and reworded items; assert ids and done marks
  - honesty: the recording brief returns `action_items`, and checklist.merge keeps the id and done mark of an item whose text matches after normalization across two extractions
- a transcript item is one Markdown file of kind transcript: the summary and key facts first, then the transcript (operator 2026-10-25)
  - instruction: tests/node/`test_transcript_render.py`: format md renders title, summary, facts, transcript; Hebrew gets dir=rtl
  - honesty: transcript.render with format md returns one Markdown document: title, summary, key facts, then the transcript segments, right to left marked for Hebrew
- transcription fires from pocketsync.recording.enriched with change created, and audio.transcribe hashes the audio itself when no `audio_sha256` is given (operator 2026-10-25)
  - instruction: tests/node/`test_audio_transcribe.py`: no `audio_sha256` hashes the file; a rule fixture on enriched with change created fires
  - honesty: audio.transcribe without `audio_sha256` computes the file's sha256 and writes under it; a rule on pocketsync.recording.enriched with data.change == created starts transcription
- one new locked brief, `recording`, returns JSON {summary, word, facts, `action_items`} for a transcript, read through `content_path` so the text never enters run state; the existing `transcript` brief and the reader book are unchanged
  - instruction: tests/node/`test_llm_complete.py`: brief recording sends the constant and parses {summary, word, facts, `action_items`}
  - honesty: the recording brief is a code constant chosen by params.brief; a step cannot replace it; tests pin its JSON shape
- generic checklist builtins over their own store collection, keyed by a list key (e.g. a recording id): `checklist.merge` merges newly extracted item texts into the stored list (an item that matches an existing one by normalized text keeps its id and done mark; a new one gets the next unused index, never reused; an existing item not found again is kept), `checklist.set` sets one item done or not done with at and by, applying it only if its at is newer than the item's last one and ignoring unknown ids, and `checklist.render` writes the Markdown (`- [ ] text <!-- todo:<id> -->`, done lines carry when and by in the comment); the list also remembers its library item id
  - instruction: tests/node/`test_checklist.py` covers merge, set (newer at wins, unknown ignored), render line shape and the stored item id
  - honesty: checklist tests pin: stable ids on re-merge, the next unused index for a new item, a stale at ignored, an unknown id ignored, and the exact Markdown line shape
- library.push takes a `format` (the file extension, e.g. md or epub) and sends the matching content type and filename; the actor lists the `formats` its library accepts, like kinds; the default stays epub so existing steps are unchanged
  - instruction: tests/node/`test_library_push.py`: format md filename and content type, unlisted format refused, default epub unchanged
  - honesty: library.push with format md uploads a .md filename with text/markdown, and refuses a format the actor does not list; without format it still sends EPUB
- library.push replaces an item when given `item_id`: it sends the bytes to the actor's `replace_path` (with `{item_id}` as one segment) with the actor's `replace_method`, and refuses `item_id` when the actor has no `replace_path`; the exact route is culture-library's to define
  - instruction: tests/node/`test_library_push.py`: `item_id` fills `replace_path` and uses `replace_method`; no `replace_path` refuses before any request
  - honesty: library.push with `item_id` calls the actor's `replace_path` with the item id filled in, and refuses `item_id` when the actor has no `replace_path`, before any request
- no todo event is ever coalesced away: a concurrency key shared by a recording's flips drops all but the newest pending event (`culture_rules`/node/firing.py), so every flip is recorded in the stored list by its own run, and the library item is always rendered from the stored list, never from the event alone
  - honesty: a test applies two flips of different lines of one list in either order and both survive in the stored list and the rendered Markdown
- a stale replace can't win: checklist.render returns the stored list's revision, and recording the pushed item (`checklist.bind`) stores the pushed revision only if newer and reports when the stored list has moved on, so a workflow can render and push again until the library holds the latest revision
  - honesty: a test binds revision 3 after revision 4 was bound: the stored pushed revision stays 4, and bind reports stale when the list is at 5
- re-running a recording's extraction gives the same items: the recording brief's parsed answer is kept beside its transcript file (keyed by the transcript sha256, as the reader book is, d4), so matching by text only matters when the transcript itself changed
  - honesty: a test runs the recording brief path twice on one transcript with different model answers and gets the first answer's items both times
- a recording with no audio is skipped, not retried: audio.transcribe fails a missing audio file with a final, non-retryable code
  - honesty: a test calls audio.transcribe with a missing file and gets a non-retryable failure code naming the missing audio
- a generic `json.read` builtin returns named fields (dotted paths, scalars only) from a JSON file that resolves under a configured root, symlinks included; a missing field is null, a file outside the root or not JSON fails before reading further
  - instruction: tests/node/`test_json_read.py`; reuse `transcript_file`'s root check
  - honesty: tests read language and `recording_at` from a meta.json fixture, return null for a missing field, and refuse a path outside the root and a symlink out of it

## Honesty conditions

- every Gwen-facing item the flows produce is a culture-library item reached through generic steps; no step writes to embodiment
- a read-only search of every live workflow, rule and actor and of the repo finds no embodiment memory ingest call
- the live daily-newspaper push sends kind newspaper with the issue EPUB whose anchors keep each result's link
- nothing in the PR names Gwen, embodiment or a recording id: Gwen appears only in the live configuration
- the before state is what scope found on disk and live on 2026-10-25 (s1-s13)
- a configured live run shows a transcript item and a todo item for one new recording, and a flip replaces the todo item under its id
- each part is checked on the live system after configuration: the enriched-to-items path, the flip under 60 s with the same id, and a re-extraction that keeps ids and done marks

## Success signals

- with the flows configured live: a recording's `enriched` event yields its transcript item and, when it has action items, its todo item with no manual step; an `embodiment.bus.todo` event changes its line within 60 s and the todo item keeps its id; re-extracting a recording keeps 100% of the ids and done marks of the to-dos it still finds
  - instruction: measure on the live system: event to items, flip to replaced item (under 60 s, same id), and a re-extraction

## Scope / boundaries

- culture-rules runs no embodiment memory ingest today: no code, no committed bundle and no live workflow, rule or actor calls `embodiment memory ingest`, so request item 5 (stop the ingest calls) has nothing to stop on culture-rules' side
  - instruction: re-run the read-only scan: list every live workflow, rule and actor and grep the repo for embodiment and memory ingest
- newspapers already keep their article links in the issue (HTML and EPUB anchors, summary links checked against the section's results) and already go to culture-library as kind newspaper; no newspaper code change
  - instruction: show the live daily-newspaper push step params and open one issue's EPUB to see the result anchors

## Assumptions

- the todo flip is configured on embodiment.bus.todo, whose data carries id, done, at (epoch s), by, `recording_id`; ordering uses at, since seq restarts per process
- Gwen's todo event carries the id from the checklist line's comment (e.g. pocket.r1.0) once Gwen reads to-dos from the library; ids in today's memory scheme (pocket.rec.`action_item`.n) are unknown to the stored lists and are ignored
- trust in a todo event is broker access: any publisher on the local broker can send embodiment.bus.todo, so the flip rule's condition checks source app://embodiment and the stored list records by

## Scope exploration

- `s1` — `live store + repo grep for embodiment/memory ingest`: 16 live workflows, 30 rules and 21 actors scanned read-only: the only match is the repo name agentculture/embodiment in github-app's list; the ingest verb is a CLI spool (embodiment/ingest/spool.py) that only embodiment's specs say culture-rules would call via machine.command
  - seeds: `c2`
- `s2` — `culture-library core/models.py, core/store.py, server/routes_library.py`: kinds are a tuple plus a DB CHECK constraint with no todo; items are immutable (sha256 UNIQUE, AUTOINCREMENT ids, no PUT/PATCH route); feed.py already has an updated event type
  - seeds: `c3` (rejected)
- `s3` — `culture_rules/node/actions/library.py + culture-library core/ingest.py`: library.push hard-codes `EPUB_TYPE` and an .epub filename; culture-library accepts epub, pdf, md, txt, json by extension with no per-kind format rule
  - seeds: `c4` (rejected)
- `s4` — `library.push replace path`: library.push only POSTs to `upload_path`; the actor already describes the library (`upload_path`, `queue_path`), so a `replace_path` fits the d8 configuration rule
  - seeds: `c5` (rejected)
- `s5` — `culture_rules/node/actions/transcript_render.py`: EPUB only; `parse_summary` accepts only {summary, word}; the book cache stores EPUB bytes keyed by the transcript sha256
  - seeds: `c6` (rejected)
- `s6` — `culture_rules/node/actions/llm.py`: `NEWSPAPER_BRIEF` and `TRANSCRIPT_BRIEF` are constants in `_BRIEFS`; a new brief is a code change by design
  - seeds: `c7` (rejected)
- `s7` — `pocketsync/pocketsync/model.py, pocket/http.py, sinks/local.py`: Recording carries summary and `action_items` from Pocket's API; meta.json, summary.md, transcript.json, transcript.md are written per recording; enrichment adds only labels and vectors
  - seeds: `c8` (rejected)
- `s8` — `culture-rules builtins (node/runner.py BuiltinCodePort)`: no generic text, Markdown, JSON or checklist builtin exists; builtins are code constants registered in runner.py
  - seeds: `c9` (rejected)
- `s9` — `culture_rules/engine (firing.py, claims.py), store/port.py, engine/variables.py`: concurrency keys coalesce by arrival order only; variables are read-only at run time; store has `update_if` CAS but no workflow-facing state builtin
  - seeds: `c10` (rejected)
- `s10` — `embodiment/embodiment/daemon/app.py _set_todo, bus.py; live events log`: published on every toggle after the state is saved; envelope id `evt-bus-<source>-<run>-<seq>`; one event already reached the spark node
  - seeds: `c11`
- `s11` — `docs/rules/pocket-transcribe rules + pocketsync events.py`: rules wait for pocketsync.recording.landed (pocketsync#12, not built); enriched carries `recording_id`, change, `content_hash`, sinks.files.audio, labels but no `audio_sha256` or language
  - seeds: `c12` (rejected)
- `s12` — `operator instruction`: PR only generic code that supports it, then outside of the spec, configure it
  - seeds: `c13`
- `s13` — `culture_rules/node/actions/newspaper_render.py + live daily-newspaper rev 5`: `_anchor` renders http(s) links per result; the push step sends kind newspaper, visibility public since 0.27.0
  - seeds: `c14`
- `s14` — `challenge pass / concurrency lens: culture_rules/node/firing.py concurrency keys`: a newer deduplicated event from any rule sharing the key replaces the pending one; two flips of different lines on one per-recording key while a run holds it lose the first flip
  - seeds: `c27`
- `s15` — `challenge pass / concurrency lens: two replace runs of one item`: two runs for one list can finish out of order; the later-finishing older snapshot would overwrite the newer one in the library
  - seeds: `c28`
- `s16` — `challenge pass / unstated-assumption lens: llm.py briefs + transcript_render.py book cache`: model output varies run to run; without a cache a rerun rewords items, which then get new ids and leave near-duplicates beside the kept originals
  - seeds: `c29`
- `s17` — `challenge pass / failure-mode lens: pocketsync meta.json (probe of 43 recordings)`: 1 of 43 recordings is `audio_absent` with no audio file; 33 have language null and 10 en; meta.json has language and `recording_at`, the enriched event has neither
  - seeds: `c30`
- `s18` — `challenge pass / adjacent-systems lens: embodiment daemon/todos.py + app.py _set_todo`: Gwen's To-dos page reads memory records and publishes their ids; the library-sourced ids differ, so the switch on Gwen's side decides which id the event carries
  - seeds: `c31`
- `s19` — `challenge pass / security lens: events-cli broker + embodiment bus.py`: the envelope source is app://embodiment and the broker is local to spark; no per-event signature exists
  - seeds: `c32`
- `s20` — `challenge pass / reversibility lens: culture-library replace (#10)`: what happens to an item's old bytes is culture-library's decision (#10); every done mark also lives in culture-rules' stored list, so the item can always be re-rendered
- `s21` — `challenge pass / observability lens: run records + library feed`: each flip is one run with its event id; latency is run start minus event time; the library feed shows an updated event per replace; clean pass, no new measure proposed

## Decisions

- transcription's language and `recording_at` come from pocketsync's meta.json through a generic JSON-read builtin (named fields from a file under a configured root, refused outside it); pocketsync's event is unchanged (operator 2026-10-25)

## Open parks

- [unknown_nonblocking] when culture-library ships the replace route and the todo kind, and its exact method and path: our replace mode is configurable, so the PR does not wait, but the live todo flip does
- [unknown_nonblocking] residual, not examined by this pass: how Gwen parses the checklist comment ids and renders Markdown transcripts (embodiment's code for that does not exist yet), and whether pocketsync can re-create a recording (a second change created) for the same audio
- [out_of_scope] embodiment's own changes: its library client still maps xteink's item JSON, its To-dos page reads memory records, and retiring the ingest mailbox is embodiment's step
- [follow_up] re-pushing the 33 existing transcripts (now EPUB document items) as Markdown transcript items, and archiving the old ones, is a live configuration run after the PR, not code
