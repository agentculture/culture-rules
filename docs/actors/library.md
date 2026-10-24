# Library service actor (`library.push`)

A library actor is a **service** actor that describes one library: where it
is, which upload path it takes, which kinds and visibilities it accepts, and
whether it has a device queue. The `library.push` action uploads an EPUB to
that library. If the library has a device queue, the action also queues the
item for a reader. Nothing about any library is built into culture-rules
(culture-rules#86).

Today's library is **culture-library** (library.culture.dev). It has no
device queue: a reader pulls from it. xteink's own library (with a queue) is
shown below as the second shape.

Read [README.md](README.md) first for the prerequisites and how secrets
work.

The secret this guide creates:

| grant name (suggested) | What it is | Used by |
|---|---|---|
| `LIBRARY_PUSH_API_KEY` | a culture-library write key | engine node |

## What the actor holds

| Param | Required | Meaning |
|---|---|---|
| `base_url` | yes | `http(s)://host[:port]` of the library, optionally with a path prefix; no credentials, query or fragment |
| `upload_path` | yes | the upload endpoint's path, e.g. `/api/library`: plain segments, no `.` or `..` |
| `kinds` | yes | the kinds this library accepts, a non-empty list of lowercase words. A push names one of them |
| `visibilities` | no | the visibilities it accepts. Without it, a push cannot send a visibility |
| `queue_path` | no | the device-queue path with `{device_id}` as one whole segment, e.g. `/api/devices/{device_id}/queue`. Unset: the push only uploads |
| `device_id` | no | the reader's device id, a positive integer, used only with `queue_path`. An action's own `device_id` wins over it |
| `api_key` | yes | `grant:<NAME>` for the library's key. A literal is refused and never echoed |

These are checked when the action runs, before any request.

## 1. Mint a key and seal it

On the culture-library host, mint a key for culture-rules alone, so it can be
revoked on its own:

```bash
culture-library keys mint --name culture-rules --scope write --kind newspaper --kind transcript
```

Then, on the host whose node runs the actor's actions:

```bash
grant set LIBRARY_PUSH_API_KEY - --hidden     # paste the key, then Enter and Ctrl-D
```

Inject it into `culture-rules-node` there (the README shows how):

```text
--inject CULTURE_RULES_SECRET_LIBRARY_PUSH_API_KEY=LIBRARY_PUSH_API_KEY
```

## 2. Create the actor

The editor's **Actors** form has no fields for these params (it keeps them
when you edit the actor there), so write the JSON and create it with the
CLI. For culture-library:

```json
{
  "id": "library",
  "name": "Culture library",
  "kind": "service",
  "machine": "<node host>",
  "params": {
    "base_url": "http://127.0.0.1:8796",
    "upload_path": "/api/library",
    "kinds": ["newspaper", "transcript"],
    "visibilities": ["private", "public"],
    "api_key": "grant:LIBRARY_PUSH_API_KEY"
  }
}
```

For xteink's library, which has a device queue, the params are:

```json
{
  "base_url": "http://127.0.0.1:8780",
  "upload_path": "/api/library",
  "queue_path": "/api/devices/{device_id}/queue",
  "kinds": ["book", "article"],
  "device_id": 3,
  "api_key": "grant:XTEINK_PUSH_API_KEY"
}
```

The action runs on the actor's machine, so a loopback `base_url` is right
when the library runs there. For a library elsewhere, set its LAN or tailnet
address in the database (`actors update`), never in committed JSON:
`scripts/scan-secrets.py` rejects a non-localhost URL there.

## 3. Use it in a rule or workflow

The `library.push` action takes:

| Param | Required | Meaning |
|---|---|---|
| `actor` | yes | the library actor's id |
| `content_base64` | yes | the EPUB, base64; at most 50 MiB decoded |
| `title` | yes | the book's title; the file is named after it |
| `kind` | yes | one of the actor's `kinds` |
| `visibility` | no | one of the actor's `visibilities`; unset, the library decides |
| `author` | no | default `culture-rules` |
| `device_id` | no | overrides the actor's `device_id`; used only with a `queue_path`, and then one of the two is needed |

The editor's action picker offers the kinds and visibilities the chosen
actor lists.

The push is one or two calls, each with `Authorization: Bearer <key>`:

1. `POST <base_url><upload_path>`, multipart: the file
   (`application/epub+zip`), `title`, `author`, `kind` and, when given,
   `visibility`.
2. Only with a `queue_path`, and only after a 2xx upload,
   `POST <base_url><queue_path>` (`{device_id}` filled in) with the item id
   the upload returned.

The output is `{item_id, created, queue_id}`. `created` is false when the
library already held the same bytes. `queue_id` is `null` without a queue
step.

A retry repeats the same calls, and the library makes them idempotent: the
upload deduplicates by content, and queueing an item that is still queued
returns the existing entry. If the reader synced the item between a lost
reply and the retry, it downloads the book once more.

## Failures

| Code | Meaning | Retried |
|---|---|---|
| `actor_kind_mismatch` | the actor is not a `service` | no |
| `invalid_actor` | an actor param above is missing or malformed (`upload_path`, `queue_path`, `kinds`, `visibilities`, `base_url`, a literal `api_key`) | no |
| `invalid_request` | a kind or visibility the actor does not list, or bad content, title or device id | no |
| `device_missing` | a `queue_path` but no `device_id` on the action or the actor | no |
| `too_large` | the EPUB is over 50 MiB | no |
| `secret_unresolved` | the key could not be resolved on the node | no |
| `upload_failed` | the library refused the upload: it names the status and the library's error code. Nothing is queued. 5xx, 408, 425 and 429 are retried | depends |
| `queue_failed` | the upload worked and the queue call failed (404 for an unknown device); it names the uploaded item id | depends |
| `network_error` | the library was unreachable or timed out | yes |

Redirects are never followed, and nothing is logged.

## In the bundles

`docs/rules/newspaper/actors/newspaper-library.json` is the culture-library
actor on machine `spark`: spark's loopback, `grant:LIBRARY_PUSH_API_KEY`, no
`queue_path`. The newspaper pushes kind `newspaper`, visibility `public`.
The transcripts on the reader (`docs/rules/pocket-transcribe`) push kind
`transcript`, visibility `private`. See
[the newspaper setup](../operations/newspaper.md).
