# Speech-to-text agent actor (`audio.transcribe`)

An STT actor is an **agent** actor (not an app actor) that points at an
OpenAI-compatible speech-to-text endpoint, such as the lobes gateway's `stt`
route. The `audio.transcribe` action reads one audio file on the actor's
machine, cuts it into windows with ffmpeg, sends each window to
`<base_url>/v1/audio/transcriptions` and writes a timestamped transcript
file. The run state and the run events carry only scalars about that file,
never the transcript text.

Read [README.md](README.md) first for the prerequisites and how secrets
work. A local server that needs no key needs no secret at all.

The secret this guide may create:

| grant name (suggested) | What it is | Used by |
|---|---|---|
| `LOBES_GATEWAY_API_KEY` | the endpoint's bearer key, if it has one | engine node |

## What the actor holds

| Param | Required | Meaning |
|---|---|---|
| `base_url` | yes | `http(s)://host[:port]`, optionally ending in `/v1`; no credentials, query or fragment. The request goes to `<base_url>/v1/audio/transcriptions` |
| `model` | yes | the model the transcript is labelled with (else the actor's own `model`). It **must match the gateway's `STT_MODEL`**: see below |
| `api_key` | no | `grant:<NAME>`, sent as `Authorization: Bearer`. A literal is refused |
| `audio_root` | yes, in practice | an absolute directory on the actor's machine. Only files under it are read; with no root every path is refused |
| `output_root` | yes, in practice | an absolute directory on the actor's machine. Transcripts are written only under it |
| `concurrency_pool` | no | a pool shared with other actors on the same server; as for [`llm.complete`](llm.md) |
| `max_concurrency` | no | how many transcriptions this actor runs at once; `1` for a shared GPU |

These are checked when the action runs; a bad one fails it with
`stt_actor_invalid` before any file is read.

**The model must match the gateway.** The lobes Whisper sidecar reads only
the `file` and `language` form fields. It ignores `model`, serves whatever its
`STT_MODEL` environment names (`ivrit-ai/whisper-large-v3-turbo` in the
`audio-he` overlay) and does not report which model answered. So the action
does not send `model` at all: it only uses it to label the result and to name
the file. Check the gateway's `STT_MODEL` when you enrol the actor, and update
the actor when the gateway changes model. Until then every new transcript is
labelled with the wrong model.

**One enabled actor per model.** An STT actor is an endpoint actor (an
`agent` with a `base_url`), so the rule in
[model-actors.md](../operations/model-actors.md) applies: a second enabled
endpoint actor with the same model fails the action with `model_taken`.

## 1. Seal the key (if the endpoint has one)

```bash
grant set LOBES_GATEWAY_API_KEY - --hidden     # paste the key, then Enter and Ctrl-D
```

Then inject it into `culture-rules-node` on the actor's machine (the README
shows how):

```text
--inject CULTURE_RULES_SECRET_LOBES_GATEWAY_API_KEY=LOBES_GATEWAY_API_KEY
```

## 2. Check ffmpeg on the actor's machine

The action calls `ffprobe` and `ffmpeg` on the node's `PATH` (no shell).
The node runs as a `systemd --user` unit, so check that user's `PATH`:

```bash
command -v ffmpeg ffprobe
```

Without them the action fails at once with `ffmpeg_missing`, naming the
binary, and sends nothing.

## 3. Create the actor

The editor's **Actors** form has no fields for these params, so write the
JSON and create it with the CLI:

```json
{
  "id": "whisper-he",
  "name": "whisper-he",
  "kind": "agent",
  "machine": "<node host>",
  "params": {
    "base_url": "http://127.0.0.1:8001",
    "model": "ivrit-ai/whisper-large-v3-turbo",
    "api_key": "grant:LOBES_GATEWAY_API_KEY",
    "audio_root": "/home/<node user>/.local/share/pocketsync/files",
    "output_root": "/home/<node user>/.local/share/culture-rules/transcripts",
    "concurrency_pool": "stt-spark",
    "max_concurrency": 1
  }
}
```

The action runs on the actor's machine: the audio file, the roots and ffmpeg
all have to be there. A loopback `base_url` is right when the gateway runs
there too. For a gateway elsewhere, set its LAN or tailnet address in the
database (`actors update`), never in committed JSON:
`scripts/scan-secrets.py` rejects a non-localhost URL there.

`actors enrol-models` cannot enrol this actor: a model id with a `/` is not
one URL segment, so it is refused. Create it by hand, with an id per model
lane.

## 4. Use it in a rule or workflow

The `audio.transcribe` action takes:

| Param | Required | Meaning |
|---|---|---|
| `actor` | yes | the STT actor's id |
| `audio_path` | yes | the audio file's absolute path, under `audio_root`. Any format ffmpeg decodes (mp3, m4a, wav) |
| `audio_sha256` | yes | the file's sha256, 64 lowercase hex characters |
| `output_dir` | yes | an absolute directory under `output_root` |
| `language` | no | a language code such as `he` or `en`, sent as the `language` field; unset, nothing is sent and the endpoint uses its default |
| `model` | no | the label for this run (else the actor's); see above |

Every other param is ignored. The steps, in order:

1. `audio_sha256` is checked before it forms any path (`invalid_request`).
2. `audio_path` is resolved with its symlinks and must lie under
   `audio_root`; `output_dir` must lie under `output_root`. A `..` escape or
   a symlink that leaves a root is refused with `path_refused` before a byte
   is read.
3. `ffmpeg` and `ffprobe` must be on `PATH` (`ffmpeg_missing`).
4. The file is hashed and must match `audio_sha256` (`audio_mismatch`), so a
   file that changed after the event is never sent.
5. **The no-op.** When the result file already exists and its
   `audio_sha256` and `model` match, its outputs are returned with no request
   and no ffmpeg run.
6. The audio is cut into windows of at most **480 s**, at a silence in a
   window's last 60 s when ffmpeg finds one, else at 480 s. Each window is
   re-encoded to 16 kHz mono 16-bit WAV, which keeps it under the sidecar's
   16 MiB upload cap and its 600 s audio cap.
7. Each window is POSTed as multipart (`file`, and `language` when given).
   Each reply is kept in a work directory, so a retried attempt (**resume**)
   sends only the windows still missing.
8. The result is written atomically (a temp file, then a rename) and the
   work directory is removed.

The result file is `<output_dir>/<audio_sha256>/transcript.<model-slug>.json`.
The slug is the model with every character outside `A-Z a-z 0-9 . _ -`
turned into `-`, so a second model writes a second file beside the first:

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

While lobes answers `{"text"}` only, each window is one segment spanning the
window. A `verbose_json` reply with `segments` (asked for in lobes-cli#328,
*planned* there) is offset by the window's start and kept as is, in the same
schema. A window whose reply is empty stays a segment with empty text and
counts in `empty_window_count`: lobes drops low-confidence chunks silently.

The output is `{result_path, result_sha256, audio_sha256, model, language,
segment_count, window_count, empty_window_count}`. `result_sha256` is the
sha256 of the file's bytes on disk.

**Timeouts.** Each request waits at most 900 s, or what is left of the
attempt. The step's own `timeout_s` bounds the attempt; the engine default
is 3600 s, so a step for long recordings sets more (the `transcribe-file`
workflow sets 14400 s). A timed-out attempt resumes on retry.

## Failures

| Code | Meaning | Retried |
|---|---|---|
| `stt_unavailable` | connection refused, DNS, timeout, a passed deadline, 5xx, 408 or 429 | yes |
| `stt_rejected` | any other non-2xx answer, 413 included | no |
| `stt_bad_response` | not JSON, or not `{text}` / `{text, segments}` | no |
| `audio_undecodable` | ffprobe or ffmpeg failed, or a 400 that says `invalid_audio` | no |
| `path_refused` | a path outside its root, a symlink out of it, a missing file, or an actor with no root | no |
| `invalid_request` | a required param missing, a malformed `audio_sha256`, `language` or `model` | no |
| `audio_mismatch` | the file's sha256 is not `audio_sha256` | no |
| `ffmpeg_missing` | `ffmpeg` or `ffprobe` is not on the node's `PATH` | no |
| `model_taken` | another enabled endpoint actor holds the same model | no |
| `actor_kind_mismatch` | the actor is not an `agent` | no |
| `stt_actor_invalid` | `base_url`, `model`, `api_key` or a root is unusable | no |
| `secret_unresolved` | the key could not be resolved on the node | no |

A failure never echoes a response body, a header value or a secret. A run
that fails ends in `rules.run.failed` with the code in `error_code`.

## In the pocket-transcribe bundle

`docs/rules/pocket-transcribe/actors/whisper-he.json` is an STT actor on
machine `spark`. It points at the lobes gateway on spark's loopback with
model `ivrit-ai/whisper-large-v3-turbo` and `grant:LOBES_GATEWAY_API_KEY`,
in pool `stt-spark` with `max_concurrency: 1`. Its roots ship as
`/home/OPERATOR/...` placeholders that you replace; see
[the pocket-transcribe setup](../operations/pocket-transcribe.md).
