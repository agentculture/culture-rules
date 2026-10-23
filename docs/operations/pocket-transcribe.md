# Pocket transcription (#73)

When pocketsync lands a Pocket recording's audio, culture-rules transcribes
the file with our own speech-to-text (Hebrew-capable, through the lobes
gateway) and writes a timestamped transcript file. Pocket's own transcript is
never read or changed. culture-rules writes only its own result file, never
pocketsync's store.

This page has two readers:

- **pocketsync**, which emits the trigger event and reads the result file
  back. The [hand-off contract](#the-hand-off-contract) below is what it
  builds against.
- **the operator**, who sets up the STT actor, imports the bundle and enables
  the rule ([setup](#1-the-stt-actor)).

The data is committed in `docs/rules/pocket-transcribe/`, in the import
format, and it ships **disabled**:

| File | What it is |
|---|---|
| `actors/whisper-he.json` | agent actor: the lobes gateway on spark's loopback, model `ivrit-ai/whisper-large-v3-turbo`, pool `stt-spark`, `max_concurrency: 1`, roots as `/home/OPERATOR/...` placeholders |
| `workflows/transcribe-file.json` | the generic file-in workflow (below) |
| `rules/pocket-transcribe.json` | event `pocketsync.recording.landed`, concurrency key `stt:{trigger.data.audio_sha256}`, placed on spark, disabled |
| `variables.json` | `transcripts_dir`, with its default and description |
| `seed-variables.sh` | seeds the variable from `variables.json` (dry run by default) |

No key, user name or non-loopback URL is committed. The key is a grant
reference, the base URL is spark's loopback, and the roots are placeholders
you replace.

**Preconditions not in this repo.** pocketsync does not emit
`pocketsync.recording.landed` yet (*planned* in pocketsync), so until it does
the rule never fires; a direct run (below) works without it. lobes answers
`{"text"}` only, so each window is one segment; finer segments arrive with
lobes-cli#328 (*planned* there) with no change here.

## The hand-off contract

```text
pocketsync ──pocketsync.recording.landed──▶ rule pocket-transcribe ──▶ workflow transcribe-file
                                                                              │
     ◀── rules.run.succeeded / rules.run.failed (data.outputs) ── result file ┘
```

### The trigger event

Type `pocketsync.recording.landed` (three segments, inside the node's event
subscription depth). Its `data` carries scalars and absolute paths only,
never transcript text. The rule reads three fields:

| Field | Required | Meaning |
|---|---|---|
| `audio_path` | yes | the audio file's absolute path on spark, under the actor's `audio_root` (pocketsync's files root) |
| `audio_sha256` | yes | the audio file's sha256, 64 lowercase hex characters |
| `language` | no | Pocket's language code (`he`, `en`), or null |

The other fields pocketsync sends (`recording_id`, `content_hash`,
`updated_at`, and the paths of `meta.json`, `transcript.json` and
`summary.md`) are ignored by this rule. They stay on the run's trigger, and
`recording_id` is how pocketsync matches a run event back to a recording
(below). The field name `audio_path` is this contract: #73 lists "the
absolute path of the audio" without naming the field.

The bus delivers at least once. A repeat of the same audio is cheap: the
concurrency key runs one transcription per `audio_sha256` at a time, and a
run that finds the result file already there makes no request (the no-op,
below).

### The workflow

`transcribe-file` knows nothing about Pocket: anything that has an audio file
path can run it. Its inputs:

| Input | Required | From the rule |
|---|---|---|
| `audio_path` | yes | `trigger.data.audio_path` |
| `audio_sha256` | yes | `trigger.data.audio_sha256` |
| `language` | no | `trigger.data.language` (null: none is sent, and the endpoint uses its default) |
| `model` | no | not mapped: the actor's model is used |
| `output_dir` | yes | the variable `transcripts_dir` |

It has one step, `transcribe` (`audio.transcribe` on `whisper-he`), with
`timeout_s` 14400 (4 hours; the longest landed recording is 3.8 hours, and
the engine default is 3600 s) and up to 3 attempts. What the action does,
step by step, is in [the STT actor guide](../actors/stt.md):

- it checks the paths against the actor's roots and the file against
  `audio_sha256`;
- it cuts the audio into windows of at most 480 s with ffmpeg, at a silence
  where it finds one;
- it sends each window, and keeps each reply, so a retried attempt
  **resumes** with the windows still missing;
- it writes the result file atomically.

### The result file

`<output_dir>/<audio_sha256>/transcript.<model-slug>.json`, where the slug is
the model with every character outside `A-Z a-z 0-9 . _ -` turned into `-`
(`ivrit-ai-whisper-large-v3-turbo`):

```json
{
  "schema": 1,
  "audio_sha256": "<64 hex>",
  "model": "ivrit-ai/whisper-large-v3-turbo",
  "language": "he",
  "duration": 1500.0,
  "segments": [{"start": 0.0, "end": 418.2, "text": "..."}],
  "created_at": "2026-10-23T08:00:00Z",
  "window_count": 4,
  "empty_window_count": 0
}
```

`start` and `end` are seconds from the start of the file. A window that came
back empty (lobes drops low-confidence audio without saying so) stays as a
segment with empty text and counts in `empty_window_count`. New fields may be
added under `schema: 1`; a reader ignores fields it does not know.

**The no-op.** When the file already exists and its `audio_sha256` and
`model` match, the run returns its outputs without a request or an ffmpeg
run. A different model writes a second file beside the first and leaves the
first as it is.

### The outputs

`rules.run.succeeded` carries the workflow's eight outputs in
`data.outputs`, all scalars:

| Output | Meaning |
|---|---|
| `result_path` | the result file's absolute path |
| `result_sha256` | the sha256 of the file's bytes: check it before reading |
| `audio_sha256` | the audio that was transcribed |
| `model` | the model the transcript is labelled with |
| `language` | the language sent, or null |
| `segment_count` | the number of segments |
| `window_count` | the number of windows the audio was cut into |
| `empty_window_count` | windows that came back with no text |

The run event's `data.rule_id` is `pocket-transcribe`; the run's trigger
(with `recording_id`) is on the run (`runs show <run id> --json`). See
[docs/run-events.md](../run-events.md) for the event's envelope.

A failed run ends in `rules.run.failed` with the code in `data.error_reason`
(`data.error_code` is the engine's `actor_failed`). The codes
(`stt_unavailable`, the only retried one, `stt_rejected`, `audio_undecodable`,
`path_refused`, `audio_mismatch`, `ffmpeg_missing`, `invalid_request`,
`model_taken` and the rest) are listed in
[the STT actor guide](../actors/stt.md#failures).

## 1. The STT actor

The actor runs on spark, where the audio, ffmpeg and the gateway are. Its
setup is in [the STT actor guide](../actors/stt.md). For this bundle:

1. **The key.** `grant:LOBES_GATEWAY_API_KEY` is the same key the newspaper's
   `cortex-spark2` uses. If spark's node already injects
   `CULTURE_RULES_SECRET_LOBES_GATEWAY_API_KEY`, there is nothing to do.
2. **The model.** `params.model` must equal the gateway's `STT_MODEL`
   (`ivrit-ai/whisper-large-v3-turbo` with the `audio-he` overlay). lobes
   ignores the model a request names, so a mismatch labels every transcript
   with the wrong model.
3. **ffmpeg.** `command -v ffmpeg ffprobe` as the node's user on spark.
4. **The roots.** Import the actor, then set its two roots to the node user's
   real, absolute paths. On spark, with the node running as `spark`, they are
   `$HOME/.local/share/pocketsync/files` (pocketsync's files root) and
   `$HOME/.local/share/culture-rules/transcripts`, with `$HOME` expanded:

   ```bash
   culture-rules actors import docs/rules/pocket-transcribe --apply
   culture-rules actors show whisper-he --json > stt-actor.json
   # edit params.audio_root and params.output_root
   culture-rules actors update whisper-he --body @stt-actor.json --apply
   mkdir -p "$HOME/.local/share/culture-rules/transcripts"
   ```

   Both roots must exist when a run starts, or it fails `path_refused`.

## 2. The variable

`transcripts_dir` is where results go. It must lie inside the actor's
`output_root`. The shipped value is `$HOME/.local/share/culture-rules/transcripts`,
and the seed script turns `$HOME/` into the home of the user who runs it, so run
it as the node's user:

```bash
bash docs/rules/pocket-transcribe/seed-variables.sh            # dry run
bash docs/rules/pocket-transcribe/seed-variables.sh --apply
```

To put results somewhere else, set it with
`culture-rules variables set transcripts_dir --value '"/abs/path"' --apply`.

## 3. Import

Seed the variable first: a rule naming an undefined variable is refused at
import. Each import is a dry run without `--apply`:

```bash
culture-rules workflows import docs/rules/pocket-transcribe --apply
culture-rules rules import docs/rules/pocket-transcribe --apply
```

## 4. Check with a direct run

Before enabling the rule, transcribe one file you pick. It needs no event:

```bash
cat > inputs.json <<JSON
{"audio_path": "$HOME/.local/share/pocketsync/files/<file>",
 "audio_sha256": "<sha256sum of the file>",
 "language": "he",
 "output_dir": "$HOME/.local/share/culture-rules/transcripts"}
JSON
culture-rules workflows run transcribe-file --inputs-json @inputs.json --apply
culture-rules runs show <run id> --json
```

A good run has `status: succeeded`, the eight outputs, and the file at
`result_path`. A recording longer than 480 s gives `window_count` of 2 or
more. Running the same file again finishes at once with the same outputs.

**Re-transcribe with another model.** Add `"model": "<model id>"` to the
inputs. The new file is named after that model, and the old one stays for
comparison. lobes serves only its `STT_MODEL`, so first switch the gateway
to that model, or point a second actor (with that model) at a gateway that
serves it.

## 5. Enable

```bash
culture-rules rules enable pocket-transcribe --apply
```

From then on, each `pocketsync.recording.landed` event starts a run on
spark. Runs queue on the `stt-spark` pool one at a time, and a step waits
for the pool as `blocked`.

## Known limits

- **Window timestamps only.** Until lobes-cli#328 ships, each window is one
  segment spanning up to 480 s.
- **The model label is the actor's word.** lobes does not report which
  model served a request; see step 1.2.
- **One machine.** The action reads the file on the actor's machine, so the
  audio must be on spark (or wherever the actor lives).
