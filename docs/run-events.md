# Run events: rules that fire when a run finishes

Deviation d21 of the `pr-fixer-rule` plan, phase 1 (the engine). A rule can
fire when another rule's run **finishes**. This is the second way rules
compose:

- relationships (*must run after*, *may run after*) chain rules that fire on
  the **same** event;
- run events chain a rule to the **end of another rule's run**.

The code is `culture_rules/node/run_events.py`, `culture_rules/node/firing.py`
and `culture_rules/events/emit.py`.

## The events

Every run that reaches a terminal status emits exactly one event into the
`events` collection. Each terminal status has its own type:

| Type | The run |
|---|---|
| `rules.run.succeeded` | succeeded: its workflow and its action |
| `rules.run.failed` | failed, after its `on_failure` action if it has one |
| `rules.run.cancelled` | was cancelled by an operator, or stopped when its rule was disabled |
| `rules.run.superseded` | was superseded: its `head_unchanged` wait saw the PR head move |

There is no shared type with a `status` field. A rule on `rules.run.failed`
(a hand-back, a re-fix) therefore never sees a superseded or cancelled run,
and there is no condition an author could forget to add.

`data` carries these fields:

| Field | Value |
|---|---|
| `run_id`, `rule_id` | the finished run and its rule |
| `workflow_id`, `workflow_version` | its pinned workflow (`null` without one) |
| `status` | `succeeded`, `failed`, `cancelled` or `superseded` |
| `concurrency_key` | the resolved key, `null` when the rule has none |
| `outputs` | **only** the outputs the workflow explicitly exports; `{}` when it produced none |
| `error_code`, `error_message` | the run's error; `null` on success |
| `trigger_event_id`, `trigger_type` | the event the run fired on; `null` for a run started by hand |
| `repository`, `number`, `head_sha`, `head_branch`, `base_sha`, `base_branch`, `base_repo`, `head_repo`, `pr_author`, `draft` | copied from the trigger's `data`, when present and scalar |

The subject fields sit at the top level, so a downstream rule can use the
same concurrency key template as the rule upstream of it, for example
`pr-fixer:{trigger.data.repository}#{trigger.data.number}`. They also carry on
down a chain. Nothing else is copied from the trigger, so free text such as a
comment body stays out.

The event's lineage:

- `causationId` is the run's trigger event;
- `correlationId` is inherited from that trigger event;
- `runId` is the finished run;
- `hops` is the trigger's hop count plus one.

The id is `runevt_` plus a digest of the run id. `time` is the run's
`finished_at`. So the event is a deterministic function of the finished run.

## Firing on them

A run event fires a rule like any other event does. The rule needs an
`event` trigger whose `params.type` is one of the run event types, plus a
normal condition over `trigger.data.*`:

```json
{
  "trigger": {"kind": "event", "params": {"type": "rules.run.succeeded"}},
  "condition": {"op": "and", "args": [
    {"op": "in", "value": {"field": "data.rule_id"}, "items": {"literal": ["pr-fix"]}},
    {"op": "compare", "cmp": "==",
     "left": {"field": "data.outputs.verdict"}, "right": {"literal": "approve"}}
  ]},
  "workflow": {"id": "publish-fix",
               "inputs": {"findings": "trigger.data.outputs.findings"}}
}
```

Its description (`rules describe`) reads:

```text
When rules.run.succeeded
If rule_id ∈ {pr-fix}
and verdict = approve
```

The editor's trigger picker offers the four types under the built-in surface
**Rules engine (a run finished)**.

Give such a rule a condition on `data.rule_id`. A rule with no condition on
`rules.run.succeeded` also fires on its **own** run's event. It then loops
until the hop cap stops it.

## Guards

Two refusals guard these events. Both are final skips, recorded on the
rule's history (`rule_decisions`):

- **`hop_limit`.** Each derived event carries `hops`: an external event has
  0, and each derivation adds one. Matching refuses any firing on an event
  with more than `MAX_EVENT_HOPS` (8) hops, or with a malformed hop count.
  The node also logs the refusal as a warning. Two rules that fire on each
  other's runs therefore stop after eight hops. No refused firing is dropped
  silently.
- **`run_event_unverified`.** Run-event types also reach `events` from the
  bus, so anyone who can publish there could claim any outputs. Before a rule
  fires on a `rules.run.*` event, the node rebuilds the event from the run it
  names and refuses on any difference. The difference can be in the run (it
  is unknown or not finished), or in the id, type, time, lineage, hop count or
  any field of `data`. A faithful copy carries the same id as the real event,
  so at most one of the two is stored and evaluated.

## Exactly once

Emission is a change-feed consumer on `runs`, `run-events`, which every node
shares and polls first in each cycle. For each run whose post-image is
terminal, one store transaction commits three things:

- the consumer's marker for that run;
- the event insert, preceded by a read of the deterministic id;
- the advanced resume token.

What this gives:

- **A node dies before the commit.** Nothing is left behind, and the next
  poll emits the event. That poll can run on any node, or on this one after a
  restart.
- **A stale node replays an old token.** It finds the marker and skips.
- **The markers are lost.** The deterministic id still absorbs the replay.
- **A pause is in force.** Emission is deferred until the pause lifts, and the
  run then emits its event once.
- **A node first runs this version.** Its first poll pins the feed's head, so
  runs that finished earlier emit nothing. An upgrade never replays history.

## Runs outside the attempt budget

The rule field `counts_toward_budget` defaults to `true`. With `false`, the
rule shares its key's one active run and its coalescing, but the attempt
budget handles it differently:

- its runs are not counted;
- the budget never refuses it;
- its `max_attempts` never becomes the key's limit.

This fits a follow-up rule that is not a fix attempt, such as a review that
fires on a fix's finished run. The field needs a `concurrency_key`, and a rule
cannot set it to `false` together with `max_attempts`. The description's
`Key` line then ends with `outside the attempt budget`.

## The pr-fixer split (*planned*)

Phase 2 of d21 (*planned*, not built) re-shapes the PR fixer on these events:

- `pr-fixer-{checks,comment,review,review-comment}` run the `pr-fix`
  workflow: quiet period, threads, agent and gate;
- `pr-fixer-review` fires on `pr-fix` succeeding and runs `review-commit`;
- `pr-fixer-refix` fires on `review = request_changes` and runs `pr-fix` with
  the findings;
- `pr-fixer-publish` fires on `review = approve` and runs `publish-fix`: push,
  pick and replies.

Only fix runs will count against the per-PR budget. Disabling
`pr-fixer-publish` will give a review-only mode. Until then, the shipped
bundle in `docs/rules/pr-fixer/` is the single-workflow fixer.
