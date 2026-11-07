# LLM agent actor (`llm.complete`)

An LLM actor is an **agent** actor (not an app actor) that points at an
OpenAI-compatible chat completions endpoint, such as a vLLM server or a
gateway in front of one. The `llm.complete` action sends it one request with
a system prompt named from the actor's own configuration (its briefs) and returns
the model's text. No prompt text lives in code. It never goes through
the coding-agent bridge, never sends tools and never names a repository,
even when the same actor also has a `bridge_url`.

Read [README.md](README.md) first for the prerequisites and how secrets
work. A local server that needs no key needs no secret at all.

The secret this guide may create:

| grant name (suggested) | What it is | Used by |
|---|---|---|
| `LOBES_GATEWAY_API_KEY` | the endpoint's bearer key, if it has one | engine node |

## What the actor holds

| Param | Required | Meaning |
|---|---|---|
| `base_url` | yes | `http(s)://host[:port]`, optionally ending in `/v1`; no credentials, query or fragment. The request goes to `<base_url>/v1/chat/completions` |
| `model` | yes | the model name to ask for (else the actor's own `model`). Name it explicitly: never rely on the server's default |
| `api_key` | no | `grant:<NAME>`, sent as `Authorization: Bearer`. A literal is refused |
| `concurrency_pool` | no | a pool shared with other actors on the same model server; see below |
| `max_concurrency` | no | how many requests this actor runs at once |

These are checked when the action runs; a bad one fails it with
`llm_actor_invalid` before any request.

**Pools.** Actors naming the same `concurrency_pool` share one slot count
across every host. The pool's cap is the smallest `max_concurrency` among
its enabled actors, so adding an actor with `max_concurrency: 1` caps the
whole pool at one. A request waits for a slot (the step shows `blocked`); past
the queue bound it fails with `queue_timeout`, and past its `not_after` it
fails with `llm_deadline` at its next re-ask.

## 1. Seal the key (if the endpoint has one)

```bash
grant set LOBES_GATEWAY_API_KEY - --hidden     # paste the key, then Enter and Ctrl-D
```

Then inject it into `culture-rules-node` on the actor's machine (the README
shows how):

```text
--inject CULTURE_RULES_SECRET_LOBES_GATEWAY_API_KEY=LOBES_GATEWAY_API_KEY
```

## 2. Create the actor

The editor's **Actors** form has no fields for these params (it keeps them
when you edit the actor there), so write the JSON and create it with the
CLI:

```json
{
  "id": "llm",
  "name": "Summaries",
  "kind": "agent",
  "machine": "<node host>",
  "params": {
    "base_url": "http://127.0.0.1:8001",
    "model": "<model name>",
    "api_key": "grant:LOBES_GATEWAY_API_KEY",
    "concurrency_pool": "<pool>",
    "max_concurrency": 1
  }
}
```

The action runs on the actor's machine, so a loopback `base_url` is right
when the server runs there. For a server elsewhere, set its LAN or tailnet
address in the database (`actors update`), never in committed JSON:
`scripts/scan-secrets.py` rejects a non-localhost URL there.

## 3. Use it in a rule or workflow

The `llm.complete` action takes:

| Param | Required | Meaning |
|---|---|---|
| `actor` | yes | the LLM actor's id |
| `content` | one of the two | the user message: a string as is, or any JSON value, serialised with sorted keys; at most 48,000 characters |
| `content_path` | one of the two | a schema-1 transcript file the port reads itself (the text never enters run state); needs `content_root` |
| `content_root` | with `content_path` | the folder `content_path` must resolve under |
| `content_max_chars` | no | how much of the transcript to send |
| `brief` | yes | the name of one of the actor's briefs (`params.briefs`, see Actor briefs); there is no default |
| `answer` | no | `{"shape": <spec>}`: read the reply as JSON and check it against the spec (see Structured answers) |
| `cache` | no | `true` with `answer` and a `content_path`: keep the first valid answer beside the file |
| `cache_name` | no | the cache file's stem (1-64 letters, digits, `-` or `_`); default `recording` |
| `cache_label` | no | the label stored in the cache file's `brief` field; default `recording` |
| `max_tokens` | no | default 1500, capped at 4000 |
| `temperature` | no | a number from 0 to 2 |
| `not_after` | no | an ISO-8601 time with a UTC offset: the work does not start after it, and the request's timeout ends at it |

Every other param is ignored. The system message is the text of the named
brief in the bound actor's `params.briefs`; a step names a brief but cannot
supply or replace its text. A step with no `brief`, or one naming a brief the
actor does not hold, fails `invalid_request` before any request.

### Structured answers

With `answer: {"shape": <spec>}`, the reply (every `<think>` block and one
Markdown fence dropped) is read as JSON and checked against the spec. The spec
is plain data in the step, so the answer's fields, types and limits are
configuration. Its types are `text`, `word`, `lines`, `int`, `bool`, `list`
and `object`; each takes `required` and `default`, text types take `strip`,
`lower`, `min_chars`, `max_chars`, `pattern` and `enum`, `lines` and `list`
take counts (`min`/`max`, `min_items`/`max_items`), and `object` takes
`fields` and `extra` (`refuse`, the default, or `drop`). For example:

```json
{"answer": {"shape": {"type": "object", "extra": "drop", "fields": {
  "summary": {"type": "lines", "required": true, "min": 1, "max": 2, "max_chars": 300},
  "word": {"type": "word", "required": true, "strip": true, "lower": true,
           "pattern": "^[A-Za-z][A-Za-z-]{1,23}$"},
  "facts": {"type": "list", "default": [], "items": {"type": "text", "strip": true}}
}}}}
```

A reply that is not JSON (or not an object for an `object` shape) fails
`answer_not_json`; one that does not fit fails `answer_invalid` naming the
field path (`facts[3]: ...`). Neither is retried, and neither is cached. A
malformed spec fails `invalid_request` before any request. The normalised
value is the output `answer`.

### The answer cache

With `cache: true`, an `answer` and a `content_path`, the first valid answer
is written beside the content file as
`answer.<cache_name>.<first 16 hex of the file's sha256>.json`. A later run
for the same file returns it without calling the model, so re-extracting a
file gives the same answer. A changed file has a new sha256 and is asked
again. The cache follows the content, not the model or the brief text: after
moving the step to another model, a cached file keeps its first answer (and
that model's name). Delete the `answer.<cache_name>.*.json` files to ask
again. A cache hit reports empty `usage`, because no tokens were spent. The
defaults (`recording`) read the files 0.41.0 wrote.

The request body has exactly `model`, `messages`, `max_tokens`,
`stream: true`, `stream_options: {include_usage: true}` and, when given,
`temperature`. It never has a `tools` field. Environment proxies are ignored
and redirects are not followed.

The reply is read as a server-sent event stream (deviation d7: always
stream). The content deltas of the first choice, joined in order, are the
text; reasoning deltas are not. The model comes from the chunks and `usage`
from the last chunk, which `include_usage` asks for. `event:`, `id:` and
`retry:` lines are ignored, before the first `data:` line too. The whole
stream must arrive within the request's timeout (a stream that stalls times
out when the time is up, not a socket timeout later) and at most 8 MiB, room
for 4000 tokens at a few hundred bytes of SSE each. An `error` event that
says the server is busy or down (a 5xx, 408 or 429 `code` or `status`, or a
`type` or `message` saying overloaded, service or temporarily unavailable,
or timeout) fails `llm_unavailable`, which is retried; a bare "unavailable",
say of a model, is not transient. A stream that ends without
`data: [DONE]`, has no content delta, or carries any other non-null `error`
fails `llm_bad_response`, like a truncated JSON reply (`"error": null` in a
chunk is no error). A plain JSON reply
(an error body, or a server that ignores `stream`) is still read as before.

The output is `{text, model, usage}`, plus `answer` (the checked value) and
`cached` when the step has an `answer`. `text` is the first choice's message,
with every `<think>` block dropped. On a cache hit (`cached: true`), `text`
is the stored answer as canonical JSON, not the model's original reply. `usage` keeps the token counts, which
count toward the actor's token budget.

## Actor briefs

An agent actor holds its briefs in `params.briefs`, a map of name to
non-empty text. They are the only place prompt text lives, so a new kind of
summary, or a change to one, is configuration rather than a release:

```json
{"briefs": {"one-line": "<the system prompt text>"}}
```

A step selects one with `brief: "one-line"`. The rules:

- A name the actor does not hold, or one whose text is empty or not a
  string, fails `invalid_request` before any request is made. There is no
  built-in brief and no default.
- No step param supplies prompt text; a step only names a brief. The request
  still has no `tools`.
- **Admins only.** Adding, changing or removing `params.briefs` on an actor is
  an admin action, because the text is a system prompt. Anyone else gets HTTP
  403 `briefs_admin_only` from the API (create and update) and from a bundle
  import. Setting the actor's other params is unchanged.
- **Before upgrading from 0.41.0**, give each actor the briefs its live steps
  use, and have every step name its brief: 0.41.0 kept three briefs in code
  and a step without `brief` used one of them. See the rollout order in
  [rolling out a release](../operations/rollout.md).

## Failures

| Code | Meaning | Retried |
|---|---|---|
| `llm_unavailable` | connection refused, DNS, timeout, a passed deadline, 5xx, 408 or 429 (a stream `error` event saying so too) | yes |
| `llm_deadline` | `not_after` has passed (checked before the pool queues the work) | no |
| `llm_rejected` | any other non-2xx answer | no |
| `llm_bad_response` | not JSON, or no message text | no |
| `answer_not_json`, `answer_invalid` | the reply does not fit the step's `answer` shape | no |
| `actor_kind_mismatch` | the actor is not an `agent` | no |
| `llm_actor_invalid` | `base_url`, `model` or `api_key` is unusable | no |
| `invalid_request`, `llm_content_too_large` | the content is empty, not serialisable or too long, the `brief` is missing or not one of the actor's, the `answer` shape or a cache param is malformed, or `not_after` is malformed | no |
| `secret_unresolved` | the key could not be resolved on the node | no |

A failure never echoes a response body, a header value or a secret.

## In the newspaper bundle

`docs/rules/newspaper/actors/cortex-spark2.json` is an LLM actor on machine
`spark`. It points at the lobes gateway on spark's loopback with model
`cortex-spark2` and `grant:LOBES_GATEWAY_API_KEY`, in pool `qwen-spark2` with
`max_concurrency: 1`, and it carries the briefs the bundle's steps name in
`params.briefs`. Any other step on that actor shares the pool, so each
summary waits behind a running one. The newspaper's summary step sets
`config.on_error: "skip"`, so a failed summary leaves that section with its
results and "Summary unavailable."; see
[the newspaper setup](../operations/newspaper.md).
