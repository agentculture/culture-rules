# Library items: transcripts and to-do lists

These are generic steps for keeping library items up to date from recordings.
The plan and spec are `docs/plans/2026-10-24-gwen-reads-the-library.md` and
`docs/specs/2026-10-24-gwen-reads-the-library.md`. The steps:

- write a recording's transcript as one Markdown item;
- keep a to-do list per recording as one Markdown checklist item;
- flip a to-do when someone marks it done, then replace the item under the
  same id.

No workflow, rule or actor using them ships in a bundle. Each deployment
configures its own flows live. That configuration names the people and
services involved, so it is never committed.

## The pieces

| Piece | Kind | What it does |
|---|---|---|
| `audio.transcribe` | action | Transcribes an audio file. Without `audio_sha256` it hashes the file itself, so an event that carries only a path is enough. A missing file fails `audio_missing`, which is final and not retried. |
| `json.read` | builtin | Reads named fields (`{output: "dotted.path"}`) from a JSON file under a `root`. Fields must be scalars; a missing one is `null`. A path outside the root (or a symlink out of it), a missing file, a file over 1 MiB, or a file that isn't JSON fails, and is final. |
| `llm.complete` with `brief: recording` | action | Asks for one JSON answer: `{summary, word, facts, action_items}`. The brief is a code constant and a step cannot change it. With `cache: true` and a `content_path`, the first valid answer is kept beside the content file (`answer.recording.<sha16>.json`). A re-run returns that answer, so re-extracting the same transcript gives the same items. |
| `transcript.render` with `format: md` | action | Writes the transcript as Markdown: the dated title as H1, the summary, `## Key facts`, then `## Transcript`. Hebrew or unlabelled transcripts are wrapped in `<div dir="rtl">`. Derived text is HTML-escaped. The first Markdown with a used summary is kept as `book.<sha16>.md`, like the EPUB. |
| `checklist.merge` | builtin | Merges extracted item texts into the stored list for a `list_key`. An item whose normalized text matches keeps its id and done mark. A new item gets the next index, and an index is never reused. A stored item not extracted again is kept. |
| `checklist.set` | builtin | Marks one item done or not done, recording `at` (epoch seconds, from 1970 to the end of 9999) and `by`. It applies only when `at` is newer than the item's last applied flip. An unknown list or id, or a flip that isn't newer, completes with `applied: false` and a `reason`. |
| `checklist.render` | builtin | Renders the list as Markdown with the list's `revision` and its bound library `item_id` (null before the first push). Each line is `- [ ] text <!-- todo:<id> -->`, or `- [x] text <!-- todo:<id> done_at=<ISO> by=<by> -->` once done. Ids are `<prefix>.<list key>.<n>`, with `n` starting at 1. |
| `checklist.bind` | builtin | Records the library item a list was pushed as, at a revision. A decimal string id is stored as the integer `library.push` takes. It keeps only a newer revision, and answers `stale: true` when the list has moved on since. |
| `library.push` with `format` / `item_id` | action | Uploads Markdown (or any format the actor lists), or replaces an item's file under its id through the actor's `replace_path`. See [the library actor guide](../actors/library.md). |

## Rules that keep a to-do list correct

- **One run per flip.** The engine coalesces events that share a concurrency
  key: while one run holds the key, a newer event replaces an older pending
  one. A flip rule that keys on a whole list could therefore drop a flip on
  another line. Key flips per item (`todo:{trigger.data.data.id}`), or not at
  all. `checklist.set` is safe to run concurrently on one list: every write is
  a compare-and-set on the list document, retried on conflict; a list still
  contended after the retries fails `checklist_contended`, which is retried.
- **Render from the stored list, never from the event.** Every flip lands in
  the store first, and the library item is always rendered from the store.
- **Push until current.** Render, push (replace), then bind with the rendered
  revision. If bind answers `stale: true`, another flip landed in between:
  render, push and bind again. A `retry_until` loop does this.
- **Trust the source.** Anyone who can publish on the broker can send a flip
  event, so the flip rule's condition checks the event's `source`.

## A worked configuration

This is a sketch of the steps, not a bundle. Placeholders in angle brackets
are each deployment's own.

1. **Transcribe.** A rule on the recorder's "recording created" event runs
   `json.read` on the recording's metadata file under its root, for its
   `language` and `recorded_at`. Then it runs `audio.transcribe` with no
   `audio_sha256`. The event's audio path may be relative to the actor's
   `audio_root`.
2. **Extract once.** On the transcription run's `rules.run.succeeded`, a
   workflow runs `llm.complete` with `brief: recording`, `cache: true` and
   `content_path` set to the transcript file.
3. **Transcript item.** `transcript.render` (`format: md`, `summary` mapped
   from the answer), then `library.push` (`format: md`, `kind: transcript`).
4. **To-do item.**
   - `checklist.merge` with `list_key` set to the recording id, `prefix`
     `<your prefix>`, and `items` mapped from the answer's `action_items`.
   - When the merge's `empty` is false: `checklist.render`, then
     `library.push` (`format: md`, `kind: todo`, `item_id` from the render).
     The render's `item_id` is the list's bound item, or null the first
     time, so the push replaces that item or uploads a new one.
   - Then `checklist.bind` with the push's `item_id` and the render's
     `revision`.
5. **Flip.** A rule on `<the reader's to-do event>`, keyed per item. It runs
   `checklist.set` with `list_key`, `id`, `done`, `at` and `by` from the event.
   When `applied` is true, it runs the render, push and bind loop above.

The library needs a `todo` kind and a replace route for steps 4 and 5. Both
are asked for in agentculture/culture-library#10. The library key must be
able to write `todo`, and the library actor needs `formats` including `md`,
plus `replace_path` and `replace_method`.
