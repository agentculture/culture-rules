# Library service actor (`library.push`)

A library actor is a **service** actor (not an app actor: xteink has no app
surface) that points at an xteink server. The `library.push` action uploads
an EPUB to xteink's library and queues it for an Xteink reader, which
downloads it at its next sync.

Read [README.md](README.md) first for the prerequisites and how secrets
work.

The secret this guide creates:

| grant name (suggested) | What it is | Used by |
|---|---|---|
| `XTEINK_PUSH_API_KEY` | an xteink `xtk_` API key | engine node |

## What the actor holds

| Param | Required | Meaning |
|---|---|---|
| `base_url` | yes | `http(s)://host[:port]` of the xteink server, optionally with a path prefix; no credentials, query or fragment |
| `api_key` | yes | `grant:<NAME>` for an `xtk_` key. A literal is refused and never echoed |
| `device_id` | no | the reader's device id, a positive integer. An action's own `device_id` wins over it |

These are checked when the action runs, before any request.

## 1. Mint a key and seal it

Any `xtk_` key is a full operator key on xteink. A key minted for this actor
alone can be revoked on its own. On the xteink host, mint one with
`create-key <name>` (for example `create-key newspaper`). Then, on the host
whose node runs the actor's actions:

```bash
grant set XTEINK_PUSH_API_KEY - --hidden     # paste the key, then Enter and Ctrl-D
```

Inject it into `culture-rules-node` there (the README shows how):

```text
--inject CULTURE_RULES_SECRET_XTEINK_PUSH_API_KEY=XTEINK_PUSH_API_KEY
```

## 2. Find the device id

xteink lists its devices at `GET /api/devices`. Note the reader's `id`.

## 3. Create the actor

The editor's **Actors** form has no fields for these params (it keeps them
when you edit the actor there), so write the JSON and create it with the
CLI:

```json
{
  "id": "library",
  "name": "Xteink library",
  "kind": "service",
  "machine": "<node host>",
  "params": {
    "base_url": "http://127.0.0.1:8780",
    "api_key": "grant:XTEINK_PUSH_API_KEY",
    "device_id": 3
  }
}
```

The action runs on the actor's machine, so a loopback `base_url` is right
when xteink runs there. For xteink elsewhere, set its LAN or tailnet address
in the database (`actors update`), never in committed JSON:
`scripts/scan-secrets.py` rejects a non-localhost URL there.

## 4. Use it in a rule or workflow

The `library.push` action takes:

| Param | Required | Meaning |
|---|---|---|
| `actor` | yes | the library actor's id |
| `content_base64` | yes | the EPUB, base64; at most 50 MiB decoded |
| `title` | yes | the book's title; the file is named after it |
| `author` | no | default `culture-rules` |
| `device_id` | no | overrides the actor's `device_id`; one of the two is needed |

The push is two calls, each with `Authorization: Bearer <key>`:

1. `POST <base_url>/api/library`, multipart: the file
   (`application/epub+zip`), `title`, `author` and `kind` = `article`.
2. Only after a 2xx upload, `POST <base_url>/api/devices/<device_id>/queue`
   with the item id the upload returned.

The output is `{item_id, created, queue_id}`. `created` is false when xteink
already held the same bytes.

A retry repeats the same two calls, and xteink makes both idempotent: the
upload deduplicates by content, and queueing an item that is still queued
returns the existing entry. If the reader synced the item between a lost
reply and the retry, it downloads the book once more.

## Failures

| Code | Meaning | Retried |
|---|---|---|
| `actor_kind_mismatch` | the actor is not a `service` | no |
| `device_missing` | no `device_id` on the action or the actor | no |
| `too_large` | the EPUB is over 50 MiB | no |
| `secret_unresolved` | the key could not be resolved on the node | no |
| `upload_failed` | xteink refused the upload: it names the status and xteink's error code. Nothing is queued. 5xx, 408, 425 and 429 are retried | depends |
| `queue_failed` | the upload worked and the queue call failed (404 for an unknown device); it names the uploaded item id | depends |
| `network_error` | xteink was unreachable or timed out | yes |

Redirects are never followed, and nothing is logged.

## In the newspaper bundle

`docs/rules/newspaper/actors/newspaper-library.json` is a library actor on
machine `spark`, pointing at xteink on spark's loopback with
`grant:XTEINK_PUSH_API_KEY`. It ships without a `device_id`: the newspaper
passes the `newspaper_device` variable on each push. See
[the newspaper setup](../operations/newspaper.md).
