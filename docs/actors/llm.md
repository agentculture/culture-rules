# LLM agent actor (`llm.complete`)

An LLM actor is an **agent** actor (not an app actor) that points at an
OpenAI-compatible chat completions endpoint, such as a vLLM server or a
gateway in front of one. The `llm.complete` action sends it one request with
a brief locked in code and returns the model's text. It never goes through
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
| `brief` | no | which locked brief: `newspaper` (default), `transcript` or `recording` |
| `cache` | no | `true` with `brief: recording` and a `content_path`: keep the first valid answer beside the file |
| `max_tokens` | no | default 1500, capped at 4000 |
| `temperature` | no | a number from 0 to 2 |
| `not_after` | no | an ISO-8601 time with a UTC offset: the work does not start after it, and the request's timeout ends at it |

Every other param is ignored. The system message is one of the briefs in
`culture_rules/node/actions/llm.py`, and a step cannot replace it:

- `NEWSPAPER_BRIEF`: summarise only the given search results, one bullet per
  item, each linking its source, and reply "No results." to an empty list.
- `TRANSCRIPT_BRIEF`: a JSON `{summary, word}` for a transcript.
- `RECORDING_BRIEF`: a JSON `{summary, word, facts, action_items}` for a
  transcript, with empty lists when there are none and nothing invented. The
  answer is checked: a reply that is not that JSON fails `answer_not_json` or
  `answer_invalid` and is never cached.

With `cache: true`, the first valid recording answer is written beside the
content file as `answer.recording.<first 16 hex of its sha256>.json`. A later
run for the same file returns it without calling the model, so re-extracting
a transcript gives the same answer. A changed file has a new sha256 and is
asked again.

The request body has exactly `model`, `messages`, `max_tokens`,
`stream: false` and, when given, `temperature`. It never has a `tools`
field. Environment proxies are ignored and redirects are not followed.

The output is `{text, model, usage}`, plus `answer` (the parsed JSON) and
`cached` for the recording brief. `text` is the first choice's message,
with a leading `<think>` block dropped. `usage` keeps the token counts, which
count toward the actor's token budget.

## Failures

| Code | Meaning | Retried |
|---|---|---|
| `llm_unavailable` | connection refused, DNS, timeout, a passed deadline, 5xx, 408 or 429 | yes |
| `llm_deadline` | `not_after` has passed (checked before the pool queues the work) | no |
| `llm_rejected` | any other non-2xx answer | no |
| `llm_bad_response` | not JSON, or no message text | no |
| `actor_kind_mismatch` | the actor is not an `agent` | no |
| `llm_actor_invalid` | `base_url`, `model` or `api_key` is unusable | no |
| `invalid_request`, `llm_content_too_large` | the content is empty, not serialisable or too long, or `not_after` is malformed | no |
| `secret_unresolved` | the key could not be resolved on the node | no |

A failure never echoes a response body, a header value or a secret.

## In the newspaper bundle

`docs/rules/newspaper/actors/cortex-spark2.json` is an LLM actor on machine
`spark`. It points at the lobes gateway on spark's loopback with model
`cortex-spark2` and `grant:LOBES_GATEWAY_API_KEY`, in pool `qwen-spark2` with
`max_concurrency: 1`. That pool is shared with the PR fixer, so each
summary waits behind a running fix. The newspaper's summary step sets
`config.on_error: "skip"`, so a failed summary leaves that section with its
results and "Summary unavailable."; see
[the newspaper setup](../operations/newspaper.md).
