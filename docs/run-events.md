# Run events: rules that fire when a run finishes

Deviation d21 of the `pr-fixer-rule` plan, phase 1 (the engine). A rule can
fire when another rule's run **finishes**. This is the second way rules
compose:

- relationships (*must run after*, *may run after*) chain rules that fire on
  the **same** event;
- run events chain a rule to the **end of another rule's run**.

The code is `culture_rules/engine/run_completions.py`,
`culture_rules/node/run_events.py`, `culture_rules/node/firing.py` and
`culture_rules/events/emit.py`.

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

### A rule whose workflow has no steps

The editor can give a rule with no workflow a stored workflow of its own
with no steps and no outputs (D7 of the
[editor fold](specs/2026-10-09-editor-rules-folded-into-workflows-three-views.md)),
so the rule shows as that workflow's entry point. The engine runs such a
rule exactly as it ran without a workflow: the same action, the same
`trigger.*` references resolved, the same outcome. Its run event differs from
the one it emitted before in two fields only, `data.workflow_id` (now the
wrapper's id, before `null`) and `data.workflow_version` (now its version,
before `null`); `outputs` stays `{}`. A rule listening on `rules.run.*` with a
`data.workflow_id` term that names some other workflow therefore starts
nothing new because of the wrapper, while one without such a term already
fired for the run before and still does. A listener that compares
`data.workflow_id` with the wrapper's own id (or `data.workflow_version` with
its version) would start firing after the conversion; check for one before
converting a rule (the fold's live conversion checked the three live
run-event rules first). `tests/engine/test_stepless_workflow.py` pins this field by field
(deviation d1 of the fold plan).

The editor also reads chains from that term: a rule on a `rules.run.*` event
whose condition compares `data.workflow_id == <workflow>` is shown as a
continuation of that workflow, and one without it reads "from any workflow".

The event's lineage:

- `causationId` is the run's trigger event;
- `correlationId` is inherited from that trigger event;
- `runId` is the finished run;
- `hops` is the trigger's hop count plus one.

The id is `runevt_` plus a digest of the run id. `time` is the run's
`finished_at`.

The event is built **once**, in the run's terminal transition. It is stored
in the same transaction as an immutable completion record in
`run_completions`, so the status change and the record commit together or not
at all. A later edit of the run document changes neither what is emitted nor
what a downstream rule is checked against.

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

Three refusals guard these events:

- **Reserved namespace.** Ingest from the bus and the webhook sink refuse
  `runevt_*` ids, `rules.run.*` types, the fixer queue's `queue_*` ids and
  `rules.queue.*` types (#35), `culture-rules://` sources, and any
  envelope that carries an `envelope` field (ambiguous with a stored event
  document). A refused envelope is never stored or evaluated. It is kept in
  `event_quarantine` with the reason and counted on the ingest result; a
  webhook carrying such a type answers the outcome `quarantined`, unlike an
  ordinary undeclared type (`ignored`). The quarantine is bounded: one record
  per refused id and reason, whose `count` grows on repeats; the payload is
  kept whole only up to 8 KiB (else a preview, its size and hash); the id,
  type and source are kept as is only up to 256 bytes (else the same preview
  form), and so is every field of a delivery-conflict record (whose id is a
  digest); a record never exceeds 16 KiB, which is checked on the final
  record; it expires 30 days after it was last
  seen (a MongoDB TTL index on `expires_at`, installed by every node and the
  API, the processes that can quarantine); and only a new record is logged.
- **Unstorable or malformed content.** The same refusal covers more than
  run events:
  - the checks settler's types and ids (d25), and schedule and probe events;
  - a checked field (`id`, `type`, `kind`, `source`) that is present but not
    a non-empty string;
  - nesting deeper than 32 levels;
  - an object key that is not a string, contains NUL or starts with `$`;
  - text that is not UTF-8 (a lone surrogate);
  - an int outside signed 64-bit.

  These are checked iteratively, before anything copies the envelope. The
  quarantine record of such a shape keeps only ASCII renderings of its id,
  type, source and reason, and the hash and preview of a bounded repr. It
  never keeps the envelope. A store may also refuse an envelope's content
  with a known content error: a driver `InvalidDocument`,
  `InvalidStringData` or `DocumentTooLarge`, an `OverflowError`, a
  `RecursionError` or a `UnicodeError`. That envelope gets the same minimal
  record, with the error's class in the reason, and the batch goes on.
  Records are deduplicated on the full id and reason. Anything else
  propagates and the batch is drained again, with the cursor not saved: a
  store outage, `OperationFailure` (a write conflict), `WriteConcernError`,
  or an unknown error. A valid event is never dropped to keep the bus
  moving. A duplicate envelope stays a counted duplicate.

  Two limits, both by design (fail closed). On the bus, an envelope that
  always raises an unknown error stalls ingest: each node cycle logs a
  warning and records the exception, and an operator must step in. In the
  webhook sink, a delivery that fails this way answers an error and stores
  nothing. GitHub does not redeliver a failed delivery on its own, so it
  stays missing until someone redelivers it (the App's advanced settings,
  or the API's `POST /app/hook/deliveries/{id}/attempts`).
- **`run_event_unverified`.** Before a rule fires on a `rules.run.*` event,
  the node compares the whole envelope, extra keys included, with the
  envelope in the run's completion record. The id must also be the one the
  record says it was emitted as. On any difference every rule on it is
  refused, with this reason recorded on its history. The record is the
  reference, never the mutable run document.
- **`hop_limit`.** Each derived event carries `hops`: an external event has
  0, and each derivation adds one. Matching refuses any firing on an event
  with more than `MAX_EVENT_HOPS` (8) hops, and also when the count is
  malformed, missing on a derived event from an engine source, or the
  envelope carries an `envelope` field. The node logs the refusal as a
  warning and records it. Two rules that fire on each other's runs therefore
  stop after eight hops.

The engine's sources are `culture-rules://…` (written straight into the
store) and `app://culture-rules/<host>` (what a node publishes on the bus).
The engine always stamps `hops` on what it derives, so a derived event from
either one without `hops` is refused.

Hop counts are only as good as their producers. An event from an outside
source with no `hops` counts as 0, even when it names a cause, because
outside producers do not count hops. A loop that passes through an outside
agent therefore resets its count.

## Exactly once

Delivery is an outbox that every node polls first in each cycle. For each
completion record not yet `emitted`, one transaction inserts the event (read
first) and moves the record to `emitted: true`, with the `event_id` it was
stored under.

What this gives:

- **A node dies inside the terminal transition.** Neither the status nor the
  record is written; the run finishes on the next tick.
- **A node dies inside delivery.** Neither the event nor the mark is written;
  the next poll, on any node, delivers it.
- **A commit's acknowledgement is lost.** The record is already `emitted` and
  is skipped; an identical stored event is accepted as delivered.
- **Two nodes race.** Both write the same record; one transaction loses.
- **A different event already holds the id.** It is quarantined as a
  conflict, and the next candidate id is tried (`<id>-genuine`,
  `<id>-genuine-2`, …), each checked the same way. The record is marked
  emitted only once the stored envelope equals the genuine one. With every
  candidate taken, the conflicts are quarantined, an error is logged and the
  record is *parked*: it leaves the main queue (so it can never starve
  healthy records) and is retried from a small parked batch, after 60 s and
  doubling up to hourly.
- **A pause is in force.** Delivery is deferred until the pause lifts.
- **Upgrade.** Only terminal transitions written by this engine have a
  record. Runs that finished earlier emit nothing, and nothing in between is
  lost, because delivery never reads change-feed history.
- **First start.** A node pins its event-trigger cursors before it delivers
  anything, at start and before every drain, so a completion pending before
  the first node started still reaches its downstream rules.
- **The event id is fixed once assigned.** Downstream run ids derive from it,
  so a completion that was delivered once is only ever re-delivered under
  that same id. If something else holds it, the record is parked until that
  exact id can be recovered; it never takes a new one.
- **Backup and restore.** Completion records are backed up with run history,
  together with the decision state: each trigger consumer's consumption mark
  for a run event, the firing intents and key reservations committed with
  it, and the runs started from them. They are scanned in that order, so a
  backed-up mark always has its intents and a started intent its run. The
  events are not backed up, so a restore re-opens every emitted completion
  whose event is missing, whatever its age, in pages, and the first node
  delivers it again under its assigned id. Then:
  - a consumer that had decided the event finds its mark and skips it, so the
    event is not re-decided against today's rules, and a rule added after
    the backup does not fire on it (like a live rule, which never backfills);
  - a consumer that had not decided it decides it now, with the rules of now,
    as it would any pending event;
  - a restored pending intent starts once, with its key reservation, and a
    started intent's run is not started again.

  Decision records are backed up too. A per-collection backup can still tear
  across collections, so a restore runs a reconciliation pass before any
  node starts: it drops key reservations no intent or running run holds, and
  re-drives the end of finished runs whose dependants were not decided yet.
  Rate windows and shared variables are not backed up; an undecided event
  meets today's (`docs/operations/backup.md`, "Restore limits").
- **A refused event is never marked consumed.** A trigger consumer records
  its consumption mark only for a run event that verifies. When the genuine
  event is first stored under an id a removed conflict had used, the stale
  trigger markers for that id are cleared, so the genuine event is
  evaluated.

The pending query is an equality query on `emitted` and `blocked` that the
store orders and limits itself (100 per poll). On MongoDB it uses a partial
index over un-emitted records only, so an empty queue costs nothing. Parked
records are read due-first: the store keeps only those whose `retry_at` has
come, ordered by it, before it limits (20 per poll; a second partial index),
so records still backing off never hide due ones. A record without the
`blocked` field (an older build) is migrated to `blocked: false` in bounded
batches before each drain, so it joins the one queue.

The change-feed consumers that remain (triggers and chains) now initialise
their cursor once, by insert: two nodes starting together agree on one
starting token.

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

A rule can also **reset** its key's budget (`resets_attempt_budget: true`;
issue #35, d32): when it fires (its trigger and condition hold), the key's counter
is reset once for that event, before the firing is admitted, exactly like a
human push or green checks. It needs a `concurrency_key`. The shipped
fixer sets it on its comment and review-comment rules, so a trusted `/fix`
starts a new story with a fresh budget. The description's `Key` line then
ends with `resets the attempt budget`.

The field is trusted configuration, not a security boundary. Whoever can save
a rule decides whether it is counted, as they decide everything else about
it. A value that is not a boolean is refused when the rule is read, and a
stored `null` is treated as counted.

## A chain is one unit per key

A run that finishes on a concurrency key **holds** the key for the rules that
would continue it (d21 phase 2, `culture_rules/engine/chain_hold.py`). Between
two stages of a chain the key used to be free: the finished run no longer held
it, and the next stage's run did not exist yet, because its event is delivered
one node cycle later. A fresh external event in that window (new checks on the
PR) took the key, and the next stage was deduplicated behind it.

Now the terminal transition, in the transaction that records the completion,
works out which live rules would fire on the run's event. That is the same
matching a trigger consumer runs: trigger type, condition, shared variables and
the hop cap. Only rules whose concurrency key resolves to the run's own key
count. With any such rule, the key's budget document gets
`hold = {run_id, rules, since}`. Then:

- **Only the continuation is admitted.** That is a firing on a `rules.run.*`
  event of exactly the held run, verified against its completion record first.
  Every other firing on the key is `deduplicated` as usual and becomes the key's
  pending event.
- **The pending event waits for the chain's end.** The continuation keeps it, so
  it fires when the chain finally ends, never in the middle.
- **The holder's end does not release the key.** Only the guard is written.
- **A continuation that does not take the key releases it.** Its condition no
  longer holds, it was disabled or deleted, or the budget refused it. The
  consumer that evaluated it removes it from the hold. When none is left the
  hold is released (`hold_released`), and the chain consumers fire the pending
  event through its rule's owner, as at a run's end.
- **A hold expires after 15 minutes** (`HOLD_TTL`). This covers a continuation
  placed on a host that never came back, so a key is never held forever. Every
  node's cycle releases expired holds the same way, so their pending events
  fire.
- **A late continuation still continues.** When the held run's own event fires
  a rule after its hold expired or was released, and no other run has taken
  the key, that rule succeeds the holder. It keeps the pending event for the
  chain's end and never coalesces it away.
- **A restore drops a hold with its reservation.** The restored run event then
  finds the key free.

A run without a concurrency key, or one nothing continues, holds nothing.

## One comment per chain

An action or `on_failure` marked `only_at_chain_end: true` is skipped
(`chain_continues`, with the continuing rules on the step's outputs) when a live
rule would continue the run's chain on its key. The run still succeeds. So only
the stage that ends a chain posts. `rules describe` adds
`(only where its chain ends)` to the `Then` / `On failure` line, and the editor
shows it as a checkbox on the action.

A `github.comment` that also sets `status: true` (d26) writes its text as the
final section of the chain's one live status comment instead of posting
(`docs/operations/pr-fixer.md`, "The status comment").

The decision is taken when the terminal step is dispatched. If the continuing
rule is disabled between that moment and the evaluation of the run's event, the
chain ends with no comment. The run's history still has it.

## The pr-fixer split (d21 phase 2)

The shipped PR fixer (`docs/rules/pr-fixer/`) is built on these events:

- `pr-fixer-{checks,comment,review,review-comment}` put the PR in the fixer
  queue (`queue-add`, #35); `pr-fixer-dispatch` runs the `pr-fix` workflow when
  the request's turn comes (a `rules.queue.dispatch` event): quiet period,
  threads, the failing SonarCloud conditions, agent and gate, one try;
- `pr-fixer-retry` puts a try whose gate did not pass back in the queue;
- `pr-fixer-review-commit` fires on `pr-fix` succeeding and runs `review-commit`
  (outside the attempt budget);
- `pr-fixer-refix` fires on `review = request_changes` and puts the PR back in
  the queue with the findings (the dispatched `pr-fix` run is the counted
  attempt);
- `pr-fixer-publish` fires on `review = approve` with a passing gate and runs
  `publish-fix`: push, threads and replies (outside the budget).

The rules share the PR's key, which the chain holds between its stages. Only
fix runs count against the per-PR budget. Disabling `pr-fixer-publish` gives a
review-only mode: the review ends the chain and posts its verdict. The review
step and `github.push` walk the chain back through these events in the store
(`culture_rules/actors/lineage.py`). The rule names differ from the d21 text in
one place: the review stage is `pr-fixer-review-commit`, because
`pr-fixer-review` already names the review-submitted trigger rule. The
operations recipe is `docs/operations/pr-fixer.md`, section 7.
