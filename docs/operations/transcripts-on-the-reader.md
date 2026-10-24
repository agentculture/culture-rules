# Transcripts on the reader

Once a Pocket recording is transcribed ([Pocket transcription](pocket-transcribe.md)),
culture-rules turns the transcript into one book on the Xteink reader:

- **Title:** `<YYYY-MM-DD> <word>`, using the recording's local date in Israel and a
  short English topic word.
- **Contents:** a one- or two-line summary in the recording's language, then the
  transcript.
- **Direction:** right to left for Hebrew.

It is the same reader the [daily newspaper](newspaper.md) goes to. The spec is
`docs/specs/2026-10-23-transcripts-on-the-reader.md`.

The data is committed in `docs/rules/pocket-transcribe/` beside the transcription lanes,
and it ships **disabled**:

| File | What it is |
|---|---|
| `workflows/transcript-to-reader.json` | the six steps below |
| `rules/pocket-to-reader.json` | fires on `rules.run.succeeded`: of `transcribe-file-he` (any language), or of `transcribe-file` when the recording is marked `en`. Concurrency key `reader:{trigger.data.outputs.audio_sha256}`, placed on spark, disabled |

A failed or cancelled transcription starts no book. If zipformer fails for a Hebrew
recording, the book waits for a re-run of `transcribe-file-he`.

## Which transcript a book uses

| Recording | Transcript in the book |
|---|---|
| marked `he`, or unlabelled (most Hebrew recordings) | zipformer (`stt-he`) |
| marked `en` | whisper (`stt-en`) |
| marked `en`, every whisper window empty | zipformer, transcribed again by the workflow's `fallback` step |

## The steps

1. `fallback` (`audio.transcribe` on `stt-he`) runs only for an `en` recording whose
   whisper windows all came back empty. It writes the zipformer transcript beside
   whisper's.
2. `keep` and `pick` (`select`) choose the transcript the book is made from.
3. `summary` (`llm.complete` on `cortex-spark2`, `brief: transcript`):
   - it reads the first 48000 characters of the transcript itself (`content_path` under
     `content_root`), so the transcript text never enters the run's state;
   - the reply is JSON `{summary, word}`;
   - it has `config.on_error: "skip"`, so when cortex is down the book still ships,
     titled `<date> recording`, with "Summary unavailable.".
4. `render` (`transcript.render`) builds one EPUB. It reads only under the transcripts
   root, and the same inputs always give the same bytes. The summary may come back
   different on a re-run, so the first book is kept beside its transcript
   (`book.<16 hex>.epub` and `.json`, named after the transcript file's sha256). A later
   run for the same transcript file sends that stored book again. A transcript that is
   transcribed again has a new sha256 and gets a new book.
5. `push` (`library.push` through `newspaper-library`) uploads the book and queues it on
   the device:
   - it uses `kind: article` for now, so the book lands in `/xteink/` named after its
     title;
   - uploading the same bytes again returns the existing item, so a repeat never adds a
     second copy.

The run's outputs are `title`, `date`, `word`, `summary_used`, `source` (`given` or
`stt-he`), `item_id`, `created` and `audio_sha256`. They hold no transcript or summary
text. The summary reply is a step output, and so is visible in the editor's Debug view.

## Setup

The actors already exist from the other bundles: `stt-he` from
[Pocket transcription](pocket-transcribe.md), and `cortex-spark2` and
`newspaper-library` from the [newspaper](newspaper.md). Nothing new to enrol.

1. Import the bundle (dry run first, then `--apply`), as for the transcription lanes:

   ```bash
   culture-rules rules import docs/rules/pocket-transcribe
   culture-rules rules import docs/rules/pocket-transcribe --apply
   ```

2. Check that the variable `newspaper_device` holds the reader's device id, as for the
   [newspaper](newspaper.md). The rule passes it to the push. Until it is set, the rule
   does not fire.
3. Enable the rule:

   ```bash
   culture-rules rules enable pocket-to-reader --apply
   ```

   The rule only fires after a transcription run, so it does nothing until the
   transcription rules run or you start a transcription yourself.

## Backfill past recordings

Run the workflow once per recording, oldest first by Pocket's `recording_at`
(`files/<yyyy>/<mm>/<id>/meta.json`), with the transcript already on disk:

```bash
cat > inputs.json <<JSON
{"result_path": "$HOME/.local/share/culture-rules/transcripts/<sha>/transcript.ivrit-ai-zipformer2-66m-streaming-he.json",
 "audio_path": "$HOME/.local/share/pocketsync/files/<yyyy>/<mm>/<id>/audio.mp3",
 "audio_sha256": "<sha>",
 "output_dir": "$HOME/.local/share/culture-rules/transcripts",
 "recorded_at": "<meta.json recording_at>",
 "language": "<meta.json language, or omit>",
 "window_count": 1, "empty_window_count": 0,
 "device_id": <newspaper_device>}
JSON
culture-rules workflows run transcript-to-reader --inputs-json @inputs.json --apply
```

- **Which `result_path`:** the zipformer file for a Hebrew or unlabelled recording, the
  whisper one (`transcript.ivrit-ai-whisper-large-v3-turbo.json`) for one marked `en`.
- **Window counts:** take them from that transcript file. Equal counts on an `en`
  recording make the workflow transcribe it again on `stt-he`.
- **Re-running is safe:** a recording already in the library comes back
  `created: false`.

## Reading and removing

The device downloads queued books on its next sync, at home or through its tunnel. To
remove a bad book, delete its library item on xteink (`DELETE /api/library/{id}`, the
`item_id` in the run's outputs). Whether that also removes it from the device depends on
whether the device is a mirror device. That hasn't been checked for this device.

## Planned

- **Its own folder** *(planned)*: a `transcript` library kind and the firmware placing
  it under `/xteink/transcripts/`, asked for in agentculture/xteink#8. Until both ship,
  books are pushed as `article` and land flat in `/xteink/`.
- **The recording date in the landed event** *(planned)*: pocketsync adding
  `recording_at` to `pocketsync.recording.landed`, asked for on agentculture/pocketsync#12.
  The transcription rules already pass it through.
