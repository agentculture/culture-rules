# The decider (`decider.assess`)

The mesh's **decider** reads a situation you describe and returns a probability for
every option of every question you pose. It never writes text, never invents an option
and never acts. A `decider.assess` step asks it from a workflow. A later step can then
branch on the answer with its `when`.

This guide is for workflow authors. It covers:

- setting up the actor;
- writing the step and branching on its answer;
- what to keep out of the request.

## The actor

The bundle `docs/rules/decider` holds one `agent` actor, `decider`:

| Param | Value | Meaning |
|---|---|---|
| `base_url` | `http://127.0.0.1:8001` | the lobes gateway on spark |
| `api_key` | `grant:LOBES_GATEWAY_API_KEY` | the gateway key, resolved at call time; never a literal |
| `domain` | `general` | which decider: the gateway routes on `X-Lobes-Model: decider:<domain>` |
| `model` | `clef` | the decider's model, for the one-actor-per-model rule |
| `concurrency_pool`, `max_concurrency` | `decider-general`, `1` | one call at a time from this engine |

The domain belongs to the actor, never to the step. A step picks a decider by naming
its actor in `params.actor`. For another domain, add another actor, one per domain.
`general` is Clef on the Jetson Orin, and the spark gateway forwards it there.

`decider.assess` is an endpoint kind, so the one-enabled-actor-per-model rule applies
(`model_taken`). For a decider the model and the domain count together (`clef@general`),
so one actor per domain is allowed and a second one for the same domain is refused.
`workflows repoint-actor` needs an endpoint actor as its target.

Import the actor with `culture-rules actors import docs/rules/decider --apply` (a dry run
without `--apply`). The bundle holds only the actor, so `rules import` and `workflows import`
find nothing to import.

## The step

Params:

- `actor` (required): the decider actor.
- `state` (required): the facts, as text.
- `questions` (required): an object of questions.
- `not_after` (optional): an ISO-8601 time with an offset. Past it, the step fails
  `decider_deadline` without asking.

Each question key is snake_case (`^[a-z][a-z0-9_]*$`) and maps to a question:

| `type` | Use for | Needs |
|---|---|---|
| `choice` | one of several mutually exclusive options | `instructions`, `criteria` (at least 2: `key: description`) |
| `noul` | a yes/no proposition | `instructions` |
| `score` | an ordinal scale, lowest first | `instructions`, `criteria` (at least 2, lowest first) |

The step sends `{"model": "clef", "state", "questions"}` to `<base_url>/v1/systemone`.
A malformed request fails `invalid_request` without being sent: an empty state, a bad
key or type, missing instructions, too few criteria, images, or a request over 48000
characters.

Outputs (declare them as ports):

| Port | Type | What |
|---|---|---|
| `answers` | object | the decider's answer, unchanged, per question |
| `picks` | object | the highest-probability pick per question |
| `host` | string | the box that answered (`local` when this gateway served it) |
| `domain` | string | the actor's domain |
| `usage` | object | `input_tokens`, `output_tokens`, `total_tokens` |

What `answers` and `picks` hold per question:

- `choice`: `answers` has `{choice, confidence, probabilities: {key: p}}`. `picks` is
  the chosen key.
- `noul`: `answers` has `{noul}`, the probability of yes. `picks` is `true` when that
  is at least 0.5.
- `score`: `answers` has `{score, confidence, legend: {"0": key, ...}, probabilities:
  {"0": p, ...}}`. `score` is the expected index, from 0. `picks` is the key of the most
  probable index, and a tie goes to the lower index.

### Branching on the answer

Wire `picks` (or `answers`) into a later step as an `object` input port, and put the
test in that step's `when`. The `when` reads only the step's own inputs:

```json
{
  "id": "go",
  "kind": "code",
  "inputs": [{"name": "picks", "type": "object", "required": false}],
  "config": {
    "builtin": "action",
    "action": {"kind": "noop", "params": {}},
    "when": {"op": "compare", "cmp": "==",
             "left": {"field": "picks.action"}, "right": {"literal": "run_as_usual"}}
  }
}
```

The text form is `trigger.picks.action == "run_as_usual"`. For a number, compare
`answers`. For example, `answers.disk_ok.noul >= 0.8` asks for a clear yes, not just a
lean.

**A missing field is false.** Suppose the decider step failed under
`config.on_error: "skip"`, or was skipped. Then a later `when` reads a field that is not
there, and every comparison on it is false, `==` and `!=` alike. Both a "yes" branch
and a "no" branch would quietly not run. When a branch must not run without an
assessment, test that it exists first:

```json
{"op": "and", "args": [
  {"op": "exists", "arg": {"field": "picks.action"}},
  {"op": "compare", "cmp": "!=", "left": {"field": "picks.action"}, "right": {"literal": "skip_tonight"}}
]}
```

## Failures

None of these carries the reply body, the key or the state:

| Code | When | Retried |
|---|---|---|
| `decider_unavailable` | network error, 5xx (the backend down or loading), 408, 429, deadline passed | yes |
| `decider_rejected` | any other status, e.g. 404 `model_not_found` (an unknown domain), 401, 409, 422 | no |
| `decider_bad_response` | the reply lacks `answers`, or an asked question's fields (another domain's shape, such as `strands-general`) | no |
| `decider_deadline` | `not_after` has passed | no |
| `invalid_request`, `decider_actor_invalid`, `model_taken` | before any request | no |

## Writing a good request

The decider ranks **your** options using **your** facts. These lessons are measured,
from the assess skill:

1. **Facts in `state`, not opinions.** A single sentence such as "the operator
   strongly prefers X" moved X from 0.17 to 0.90.
2. **Give it the numbers:** sizes, budgets, what already failed, what is reversible.
3. **Make options concrete, mutually exclusive and covering.** Include "keep
   current" and the cautious option, because an option you did not write cannot win.
4. **Stable snake_case keys.** They come back in `answers` and `picks`.
5. **One decision per `choice`.** Add `noul` questions for premises you are unsure of.
6. **Read the distribution.** 0.57 against 0.23 is a lean, and 0.8 or more is a clear
   preference. Use `answers` when the margin matters.

The decider is deterministic: the same request gives the same numbers. A call takes
about 1–2 s.

## What not to send

The state is forwarded across the mesh to the box that hosts the domain (`general`: the
Orin). Keep secrets, credentials and private personal data out of it. The step never
writes the state to its outputs, its failures or a log.
