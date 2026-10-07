"""Event -> rule -> run: how a node evaluates rules and turns firing decisions into runs.

Lifted from the proven multi-host harness design (t39) into production code:

* every host ingests every event from its own subscription (the ``events`` collection
  dedups by envelope id);
* **placed rules** (``Rule.placement`` set, deviation d1) are evaluated through a per-host
  trigger consumer ``triggers@<host>`` whose handler only looks at rules whose placement
  (:func:`~culture_rules.engine.placement.resolve_rule_placement` over enrolled machines,
  heartbeats and drain flags) resolves to *this* host - so a rule placed on B is evaluated
  on B and nowhere else, and a stopped B resumes its own backlog when it restarts;
* **unplaced rules** are evaluated through one shared consumer ``triggers``: whichever
  host fires an event first commits the fire marker, so each event is evaluated once;
* a firing decision is committed in the trigger transaction as a ``rule_fires`` intent
  (id = :func:`~culture_rules.engine.claims.firing_key`), and an eligible host turns a
  pending intent into a run whose id is derived from ``(rule, event)``
  (:func:`run_id_for`) - a second start is a duplicate key, so a rule fires exactly once
  per event even when two hosts race or one dies between the fire and the start. A placed
  rule's run starts on the host that evaluated it; an unplaced rule's on any host.

Stranded placed intents
=======================
Only its evaluating host starts a placed rule's intent. If that host dies between the fire
and the start and stays offline (no heartbeat - or, never having beaten, no firing - for
:data:`~culture_rules.engine.runs.PLACEMENT_ABANDON_AFTER`, 10 minutes), any node marks the
intent ``failed`` with ``placement_unavailable`` (``abandoned_by``/``abandoned_at`` noted).
A failed intent is a run that never started: it frees the concurrency key it reserved, its
key's pending deduplicated event fires through the chain feed's ``rule_fires`` source, and
its must-after dependants settle. A host that comes back after that finds the intent failed
and does not start it late; a host that still beats, however slow, keeps its intent. (A
host that started the run but died before marking the intent: the run exists, so the
intent is marked ``started`` instead.) A start and an abandonment never both commit: the
run is inserted in one transaction with the intent's ``pending`` -> ``started`` move, fenced
on the intent still being ``pending`` and still holding its key's reservation, and the
abandonment is a ``pending`` -> ``failed`` compare-and-set - whichever writes the intent
first wins, the other's write fails or conflicts and it does nothing. So a host resuming
after its intent was abandoned (and the key admitted another run) never starts a second run
on the key.

Drained or offline host (the t39 gap, fixed here)
=================================================
When a placed rule *would* resolve to this host but this host is currently drained or
seen offline, the event is **not** consumed: the evaluation raises :class:`Deferred`,
the trigger transaction rolls back and the per-host cursor stays before that event, so
the host evaluates it once it is back in service (undrained, or beating again). Events
behind it for this host's placed rules wait too (in order); unplaced rules are unaffected.
A placement that can never succeed (unknown machine, unmet requirement, ...) is not
deferred: no host would ever take it.

Matching runs over the whole live rule snapshot (so supersede and exclusive groups see
every rule); each consumer then acts only on the decisions for its own rules. An intent
that already exists (another host fired the same rule for the same event after a
placement change) is left alone. A skip whose reason matters to the operator
(superseded_by, blocked_by_predecessor, group_lost) is persisted in the same transaction as
a ``rule_decisions`` record (:mod:`culture_rules.engine.decisions`), so a rule's contextual
history can say "superseded by A".

Rule chains (must run after / may run after)
============================================
Matching is given the **run facts** of the event: for every rule that is somebody's
predecessor, its run for this event (looked up directly by :func:`run_id_for`) or, before
the run exists, its firing intent. :func:`~culture_rules.engine.chaining.sequence` then
decides whether a dependant fires now, waits (``blocked_by_predecessor``) or will never
fire for this event (``predecessor_failed``); its module docstring states the must/may
semantics. A firing dependant's intent carries ``upstream`` - each predecessor's
explicitly exported outputs - which the run exposes as ``rules.<id>.outputs.*`` to the
workflow inputs mapping and the action params.

A waiting dependant is re-evaluated when its predecessor settles for the event, through two
more consumers per host built on the same placed/shared split - ``chain@<host>`` for
dependants placed on this host, ``chain`` shared for unplaced ones
(:class:`~culture_rules.node.chain.FeedConsumer`). They watch:

* ``runs``: a run reaching ``succeeded`` / ``failed`` / ``cancelled``;
* ``rule_decisions``: a final skip - a waiting record settled as one, or a record written
  final in one step (``predecessor_failed``, ``rate_capped``, ...: no ``superseded``
  history), so a skip cascades down a chain (A failed -> B skipped -> C skipped) also when
  the intermediate rule is placed on another host and decided only after A finished;
* ``rule_fires``: an intent whose run could not start.

Each such change is handled exactly once per consumer (a marker plus the cursor commit with
the handler's writes), and the handler re-evaluates only the rules naming that predecessor
in ``must_after`` / ``may_after`` that belong to the consumer, for the same trigger event.
A dependant still fires at most once per event: its intent id is
:func:`~culture_rules.engine.claims.firing_key` of (rule, event) whichever path fires it,
and the re-evaluation always writes the (rule, event) decision record, so it conflicts with
a concurrent first evaluation of the same rule rather than racing past it. The waiting
record is superseded by the outcome (:func:`~culture_rules.engine.decisions.settle_decision`).

Shared variables
================
A rule may reference shared variables (``vars.<name>`` in its condition, ``{"$var": name}``
in its workflow inputs; :mod:`culture_rules.engine.variables`). Each evaluation reads the
current values of the referenced variables inside the trigger transaction and hands them
to matching, so the next event after a variable changes sees the new value with no rule
edited. A firing intent carries the values its rule references (``variables``); the run
maps its ``{"$var": name}`` inputs from that snapshot, so the inputs are the values at
firing time - the same the condition saw.

Fail closed: a node built with ``variables=False`` (it does not advertise the
``variables`` capability) never evaluates such a rule - matching records the final skip
``variables_unsupported`` - and a variable that is not defined gives
``variable_undefined``; neither reads the reference as missing (``not(a in vars.x)`` would
be true). An intent for a variable-referencing rule that carries no ``variables`` snapshot
was written by a node that did not resolve variables (an older binary): this node refuses
to start it and marks it failed (``variables_unsupported``).

Pause
=====
A global pause (:meth:`~culture_rules.engine.runs.Containment.pause`) treats the two paths
differently, on purpose:

* **a new trigger event that arrives while paused is dropped** (spec h175: "after global
  pause, a matching event fires nothing"). The trigger consumers evaluate it, every rule
  decides ``paused`` (not recorded), and the fire marker and cursor commit: it does not
  fire after resume either;
* **a chain re-evaluation is deferred, not dropped.** A dependant already waiting
  (``blocked_by_predecessor``) was accepted for its event before the pause; its
  predecessor settling during the pause must not finalize it as a ``paused`` skip. The
  chain handler raises :class:`Deferred` instead, so the transaction rolls back: no
  marker, no decision write, and that feed's cursor stays before the settle. Every poll
  during the pause defers it again (reported on the cycle's ``deferred``); the first poll
  after resume re-evaluates it exactly once, as if the settle had just happened. The state
  is the persisted cursor itself, so a node restart during the pause changes nothing.
  Later changes on the same feed wait behind it, in order - harmless, since nothing can
  fire while paused.

Rate cap
========
A rule fires at most ``trigger.params.max_fires_per_hour`` times (default
:data:`DEFAULT_MAX_FIRES_PER_HOUR`; a value that is not a positive integer falls back to it)
in any trailing hour, counted across every host sharing the store. Over the cap, a matching
rule records the final skip ``rate_capped`` (:mod:`culture_rules.engine.decisions`) instead
of a firing intent.

* The count lives in one small ``rule_rates`` document per rule: the timestamps (this
  host's clock at evaluation) of its fires in the trailing hour, pruned on every write and
  never longer than the cap. Reading it is one ``get``; it does not scan ``rule_fires``.
* Every fire rewrites that document in the same transaction as the intent. Two
  transactions firing the same rule concurrently therefore write the same document, and
  the store's write conflict (MongoDB: snapshot isolation would otherwise let both read
  "under the cap" and both commit, write skew) rolls one back; its change is retried by
  the next poll and sees the other's fire. Trigger evaluations of one consumer already
  serialise on that consumer's cursor document; the window is what serialises fires that
  arrive through *different* consumers - the trigger path and a chain re-evaluation, or
  a placed rule whose placement moved between hosts.
* The cap is applied to the matching decisions *before* sequencing, so a dependant in the
  same evaluation sees a capped predecessor as "did not fire" (``predecessor_failed`` for
  must run after; a may-run-after dependant fires without it). A rule that would then wait
  for a may-run-after predecessor is capped when it matches.
* ``rate_capped`` is sticky per (rule, event): once recorded, every later evaluation of
  that rule for that event (a redelivery, a chain re-evaluation, a dependant on another
  host) treats it as capped, even after the window has room again. A host evaluating a
  dependant of a predecessor placed elsewhere cannot know the predecessor's cap outcome
  before it is recorded, so the dependant waits; the predecessor's ``rate_capped`` record
  is final in one step and settles it through the chain feed.

Concurrency keys
================
A rule with a ``concurrency_key`` template (``Rule.concurrency_key``, e.g.
``pr-fixer:{trigger.data.repository}#{trigger.data.number}``) holds that key from the moment
it fires until its run ends. **The key is global** (deviation d13): rules whose templates
resolve to the same string share one active run, one attempt budget and one coalescing slot
- the pr-fixer is four rules (settled checks, a comment, a review, a review comment) on one
key per PR. A rule that wants isolation uses a distinct template, by convention a namespace
prefix (``pr-fixer:``). :mod:`culture_rules.engine.claims` keeps one
``rule_attempt_budgets`` document per resolved key, written in the trigger transaction:

* **one active run per key.** A firing whose key is held by a pending intent or a live run
  is recorded as ``deduplicated`` (its detail names the holding run) and remembered as the
  key's ``pending_event_id`` with the rule that recorded it; a newer deduplicated event, from
  any rule sharing the key, replaces it. When the holding run ends (any terminal status,
  ``superseded`` included) or its intent fails to start, the chain consumer that owns the
  recording rule releases the key and re-decides that newest event once, through that rule
  - so a human push arriving while a run sleeps in its quiet-period wait is handled after
  the stale run supersedes itself, never dropped. (Coalescing, not preemption: a newer
  event never cancels the run holding the key.) Every chain consumer handling the end
  writes the budget while the run still holds it, owner or not - the release, or a guard
  that leaves the pending event to its owner - so a concurrent trigger transaction noting a
  deduplicated event conflicts with each of them and re-runs (admitting its event) unless
  it committed first, and then every consumer, the owner's included, sees its event;
* **an attempt budget.** Every admitted run counts, whatever its outcome - a fixer whose
  own push produces new failing checks must not loop. After ``max_attempts`` admissions
  further firings are recorded as ``attempt_budget_exhausted``. Rules sharing a key share
  the count; the limit is the smallest ``max_attempts`` declared among the live rules whose
  template resolves to that key on the firing event (a rule without one is bounded by the
  others; nothing is refused at save time). Only an explicit signal
  resets the counter (an outstanding reservation is kept): a ``github.pr.synchronize``
  whose ``data.self_authored`` is explicitly false (the hook sink tags every event once the
  app actor names its ``self_identity``), or a ``github.pr.checks_settled`` with
  ``data.conclusion`` ``"success"``. It resets each distinct key once per event: the keys
  that this consumer's keyed rules triggered by a ``github.*`` event resolve on the reset
  event, whatever the event's own type. A reset applies once per (key, event) whatever the
  order consumers reach it in (a marker per pair, see ``reset_attempt_budget``), so a
  lagging consumer never grants an attempt without a new signal;
* **fail closed per rule.** A key that does not resolve on a firing event (a missing or
  non-scalar value) records the final skip ``concurrency_key_unresolved``; other rules on
  the same event, and later events, are unaffected;
* **admission skips reach dependants.** Admission runs after sequencing, so a dependant in
  the same evaluation waits; every later evaluation of that event (the chain re-evaluation
  the skip record triggers included) applies the recorded final admission skip -
  ``attempt_budget_exhausted``, ``concurrency_key_unresolved``, or a ``deduplicated``
  event marked ``coalesced`` once a newer one displaced it as the key's pending event (by
  being deduplicated in its place, or admitted before the holder's completion was
  handled, which clears the slot) - to
  the predecessor *before* sequencing, so a must-after dependant settles as
  ``predecessor_failed`` and a may-after one fires without it. A deduplicated event still
  pending keeps its dependants waiting: it fires once the holding run ends. A holding
  run's end is processed even after its rule was deleted or lost its key.

Standard-library only.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from culture_rules.engine.chaining import LIVE, START_FAILED, sequence
from culture_rules.engine.claims import (
    RULE_ATTEMPT_BUDGETS,
    budget_id,
    firing_key,
    guard_concurrency,
    note_deduplicated,
    release_concurrency,
    reserve_concurrency,
    reset_attempt_budget,
    resolve_concurrency_key,
)
from culture_rules.engine.decisions import (
    FINAL_SKIP_REASONS,
    RATE_CAPPED,
    RULE_DECISIONS,
    decision_key,
    settle_decision,
)
from culture_rules.engine.matching import (
    ATTEMPT_BUDGET_EXHAUSTED,
    BLOCKED_BY_PREDECESSOR,
    CONCURRENCY_KEY_UNRESOLVED,
    DEDUPLICATED,
    FIRE,
    VARIABLES_UNSUPPORTED,
    Decision,
    RuleOutcome,
    RunFacts,
    TriggerMatcher,
    match,
    trigger_matches,
)
from culture_rules.engine.placement import MachineState, Resolved, resolve_rule_placement
from culture_rules.engine.runs import (
    FATAL_PLACEMENT,
    PLACEMENT_ABANDON_AFTER,
    PLACEMENT_UNAVAILABLE,
    RUN_DONE,
    RUNS_COLLECTION,
    Executor,
    RunError,
    drained_machines,
    is_paused,
)
from culture_rules.engine.variables import variable_values
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.events.triggers import FIRES_COLLECTION, EventTriggers
from culture_rules.machines.enrol import enrolled_machines
from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION, online_machines
from culture_rules.model.actor import Actor
from culture_rules.model.rule import Rule
from culture_rules.model.variable_refs import rule_variable_refs
from culture_rules.model.workflow import Workflow
from culture_rules.node.chain import FeedConsumer, Source, live_rules
from culture_rules.ops.logs import log_context
from culture_rules.store.port import (
    DuplicateKeyError,
    StoragePort,
    StoreOps,
    TransientStoreError,
)
from culture_rules.store.versioning import utc_timestamp

__all__ = [
    "DEFAULT_MAX_FIRES_PER_HOUR",
    "RULE_FIRES",
    "RULE_RATES",
    "SHARED_CHAIN",
    "SHARED_CONSUMER",
    "Deferred",
    "RuleFiring",
    "max_fires_per_hour",
    "placed_chain",
    "placed_consumer",
    "run_id_for",
]

log = logging.getLogger("culture_rules.node.firing")


RULE_FIRES = "rule_fires"
"""Firing intents: one per (rule, event) that matched, committed with the trigger fire."""
RULE_RATES = "rule_rates"
"""Per-rule fire windows: the timestamps of a rule's fires in the trailing hour (rate cap)."""
DEFAULT_MAX_FIRES_PER_HOUR = 60
"""The fire-rate cap of a rule whose trigger sets no valid ``max_fires_per_hour``."""
RATE_WINDOW = timedelta(hours=1)
SHARED_CONSUMER = "triggers"
"""The trigger consumer every host shares for unplaced rules."""
SHARED_CHAIN = "chain"
"""The chain consumer every host shares for unplaced dependants."""


def placed_consumer(host: str) -> str:
    """The per-host trigger consumer for rules placed on ``host``."""
    return f"triggers@{host}"


def placed_chain(host: str) -> str:
    """The per-host chain consumer for dependants placed on ``host``."""
    return f"chain@{host}"


def max_fires_per_hour(rule: Rule) -> int | None:
    """``rule``'s fire-rate cap: ``trigger.params.max_fires_per_hour`` when it is a positive
    integer; otherwise :data:`DEFAULT_MAX_FIRES_PER_HOUR`, except that a ``schedule``
    trigger is uncapped (``None``) by default, since its cron already bounds its cadence and
    an every-minute cron would otherwise be capped by clock jitter (deviation d2)."""
    value = rule.trigger.params.get("max_fires_per_hour")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None if rule.trigger.kind == "schedule" else DEFAULT_MAX_FIRES_PER_HOUR


def run_id_for(rule_id: str, event_id: str) -> str:
    """The run id of ``rule_id`` firing on ``event_id`` (the same on every host)."""
    digest = hashlib.sha256(json.dumps([rule_id, event_id]).encode()).hexdigest()
    return f"run-{digest[:32]}"


class Deferred(Exception):
    """A placed rule belongs to this host but the host is drained/offline: keep the event."""

    def __init__(self, rule_id: str, event_id: str, reason: str) -> None:
        super().__init__(f"rule {rule_id!r} on event {event_id!r} deferred: {reason}")
        self.rule_id = rule_id
        self.event_id = event_id
        self.reason = reason

    def to_dict(self) -> dict[str, str]:
        return {"rule": self.rule_id, "event": self.event_id, "reason": self.reason}


@dataclass
class PollOutcome:
    """What one consumer poll committed (or kept back)."""

    evaluated: list[tuple[str, str]] = field(default_factory=list)
    deferred: list[dict[str, str]] = field(default_factory=list)
    error: Exception | None = None
    """A failure that stopped the poll part-way; ``evaluated`` still lists what committed."""


class RuleFiring:
    """One host's two trigger consumers plus the intent starter."""

    def __init__(
        self,
        store: StoragePort,
        host: str,
        executor: Executor,
        *,
        clock: Callable[[], datetime],
        variables: bool = True,
    ) -> None:
        self.store = store
        self.host = host
        self.executor = executor
        self._clock = clock
        self.variables = variables
        """Whether this node resolves shared variables (its ``variables`` capability)."""
        self._pending: dict[str, list[tuple[str, str]]] = {}
        self.placed = EventTriggers(
            store,
            lambda tx, ev: self._evaluate(tx, ev, placed=True),
            host=host,
            consumer=placed_consumer(host),
            handler_collections=(RULE_FIRES, RULE_DECISIONS, RULE_RATES, RULE_ATTEMPT_BUDGETS),
            clock=clock,
        )
        self.shared = EventTriggers(
            store,
            lambda tx, ev: self._evaluate(tx, ev, placed=False),
            host=host,
            consumer=SHARED_CONSUMER,
            handler_collections=(RULE_FIRES, RULE_DECISIONS, RULE_RATES, RULE_ATTEMPT_BUDGETS),
            clock=clock,
        )
        self.chain_placed = self._chain(placed_chain(host), placed=True)
        self.chain_shared = self._chain(SHARED_CHAIN, placed=False)

    def _chain(self, consumer: str, *, placed: bool) -> FeedConsumer:
        def handle(kind: str) -> Callable[[StoreOps, Mapping[str, Any], str], None]:
            return lambda tx, doc, marker: self._settled(tx, kind, doc, marker, placed=placed)

        return FeedConsumer(
            self.store,
            (
                Source(RUNS_COLLECTION, _finished_run, handle("run")),
                Source(RULE_DECISIONS, _settled_skip, handle("decision")),
                Source(RULE_FIRES, _failed_intent, handle("intent")),
            ),
            host=self.host,
            consumer=consumer,
            handler_collections=(
                RULE_FIRES,
                RULE_DECISIONS,
                RULE_RATES,
                RULE_ATTEMPT_BUDGETS,
                RUNS_COLLECTION,
                EVENTS_COLLECTION,
            ),
            clock=self._clock,
        )

    @property
    def consumers(self) -> tuple[EventTriggers | FeedConsumer, ...]:
        """Every consumer a node polls each cycle, in order."""
        return (self.placed, self.shared, self.chain_placed, self.chain_shared)

    # ------------------------------------------------------------------ polling

    def poll(self, consumer: EventTriggers | FeedConsumer) -> PollOutcome:
        """Poll one consumer; report committed evaluations, a deferral, and any error.

        A failure part-way (e.g. a transient conflict on one event) is returned on
        ``outcome.error`` rather than raised, so the evaluations committed before it are
        still reported; the failed event is retried by the next poll.
        """
        outcome = PollOutcome()
        self._pending.clear()
        try:
            consumer.poll()
        except Deferred as deferred:
            outcome.deferred.append(deferred.to_dict())
            log.info("%s", deferred)
        except Exception as exc:  # noqa: BLE001 - handed back to the caller on the outcome
            outcome.error = exc
        finally:
            # Only evaluations whose transaction committed count: the committed fire marker
            # names its host. (A later event's failure must not hide an earlier commit.)
            for marker_id, evaluated in self._pending.items():
                marker = self.store.get(FIRES_COLLECTION, marker_id)
                if marker is not None and marker.get("host") == self.host:
                    outcome.evaluated += evaluated
            self._pending.clear()
        return outcome

    # ------------------------------------------------------------------ evaluation

    def _snapshot(self, tx: StoreOps) -> tuple[list, list[MachineState], list[Actor]]:
        machines = enrolled_machines(tx)
        online = online_machines(tx, self._clock())
        drained = drained_machines(tx)
        states = [MachineState(m.name, m.name in online, m.name in drained) for m in machines]
        actors = [Actor.from_dict(d, strict=False) for d in tx.find("actors")]
        return machines, states, actors

    def _mine(self, rule: Rule, snapshot: tuple, event_id: str) -> bool:
        """Whether placed ``rule`` is this host's; raises :class:`Deferred` when it is but
        this host is temporarily out of service."""
        machines, states, actors = snapshot
        resolved = resolve_rule_placement(rule, machines, states, actors)
        if isinstance(resolved, Resolved):
            return resolved.machine == self.host
        if resolved.code in FATAL_PLACEMENT:
            return False  # no host can ever take it; nothing to wait for
        # Would it be ours if this host were online and undrained?
        available = [
            MachineState(s.name, True, False) if s.name == self.host else s for s in states
        ]
        if not any(s.name == self.host for s in states):
            return False
        hypothetical = resolve_rule_placement(rule, machines, available, actors)
        if isinstance(hypothetical, Resolved) and hypothetical.machine == self.host:
            raise Deferred(rule.id, event_id, f"{resolved.code}: {resolved.message}")
        return False

    def _live_rules(self, tx: StoreOps) -> list[Rule]:
        return live_rules(tx.find("rules"))

    def _ours(self, tx: StoreOps, rules: list[Rule], event_id: str, *, placed: bool) -> set[str]:
        """The rules this consumer evaluates (raises :class:`Deferred`, see ``_mine``)."""
        if placed:
            placed_rules = [r for r in rules if r.placement is not None]
            if not placed_rules:
                return set()
            snapshot = self._snapshot(tx)
            return {r.id for r in placed_rules if self._mine(r, snapshot, event_id)}
        return {r.id for r in rules if r.placement is None}

    def _evaluate(self, tx: StoreOps, event: Mapping[str, Any], *, placed: bool) -> None:
        envelope = event["envelope"]
        event_id = envelope["id"]
        consumer = placed_consumer(self.host) if placed else SHARED_CONSUMER
        marker_id = f"{consumer}/{event_id}"  # EventTriggers.fire_id
        self._pending[marker_id] = []  # a retried transaction re-evaluates from scratch
        rules = self._live_rules(tx)
        ours = self._ours(tx, rules, event_id, placed=placed)
        if _resets_budgets(envelope):
            # Once per resolved key (keys are global, shared by every rule resolving them),
            # and once per event across consumers (``reset_attempt_budget`` notes the event).
            for key in sorted(_reset_keys(rules, ours, envelope)):
                reset_attempt_budget(tx, key, event_id)
        if ours:
            self._decide(tx, envelope, rules, ours, marker_id, placed=placed, chained=False)

    def _settled(
        self, tx: StoreOps, kind: str, doc: Mapping[str, Any], marker_id: str, *, placed: bool
    ) -> None:
        """A predecessor settled for an event: re-evaluate its dependants that are ours."""
        self._pending[marker_id] = []
        if kind == "run":
            predecessor = (doc.get("rule") or {}).get("id")
            envelope = doc.get("trigger") or {}
        elif kind == "intent":
            predecessor, envelope = doc.get("rule_id"), doc.get("trigger") or {}
        else:
            predecessor = doc.get("rule_id")
            stored = tx.get(EVENTS_COLLECTION, doc.get("event_id") or "")
            envelope = (stored or {}).get("envelope") or {}
        event_id = envelope.get("id")
        if not predecessor or not event_id:
            return
        rules = self._live_rules(tx)
        if kind in ("run", "intent"):
            holding = doc.get("id") if kind == "run" else doc.get("run_id")
            if holding:
                self._fire_coalesced(tx, holding, event_id, rules, marker_id, placed=placed)
        dependants = {r.id for r in rules if predecessor in (*r.must_after, *r.may_after)}
        if not dependants:
            return
        ours = self._ours(tx, [r for r in rules if r.id in dependants], event_id, placed=placed)
        if not ours:
            return
        if is_paused(tx):
            # Not an outcome: keep the waiting record, the marker and the cursor unwritten
            # so the re-evaluation happens once the pause lifts (module doc, "Pause").
            raise Deferred(", ".join(sorted(ours)), event_id, "paused: re-evaluated on resume")
        self._decide(tx, envelope, rules, ours, marker_id, placed=placed, chained=True)

    def _fire_coalesced(
        self,
        tx: StoreOps,
        holding: str,
        event_id: str,
        rules: list[Rule],
        marker_id: str,
        *,
        placed: bool,
    ) -> None:
        """The run ``holding`` a concurrency key ended (or never started): release the key
        and fire the newest event deduplicated meanwhile, once (module doc, "Concurrency
        keys"). Only the consumer that owns the rule does it; every other one writes the
        budget too (:func:`~culture_rules.engine.claims.guard_concurrency`)."""
        by_id = {r.id: r for r in rules}
        for budget in tx.find(RULE_ATTEMPT_BUDGETS, {"run_id": holding}):
            # The pending event is fired through the rule that recorded it (keys are shared
            # across rules), so that rule's consumer releases; with none pending, the
            # holder's consumer does.
            owner = by_id.get(budget.get("pending_rule_id") or "") or by_id.get(
                budget.get("rule_id") or ""
            )
            if owner is None or not self._ours(tx, [owner], event_id, placed=placed):
                # Not ours to release (or nobody's: the rules are gone). Still write the
                # budget, so a trigger transaction about to record a pending event behind
                # this run conflicts with this one too - else it could commit one that the
                # owner's consumer, done with this run already, never fires (r18, r19).
                guard_concurrency(tx, budget["id"], holding)
                continue
            pending = release_concurrency(tx, budget["id"], holding)
            if pending is None:
                continue
            pending_event, pending_rule = pending
            rule = by_id.get(pending_rule)
            if rule is None:
                continue  # the recording rule is gone: nothing to fire
            if is_paused(tx):
                # Accepted before the pause, like a waiting dependant: defer, never drop.
                raise Deferred(rule.id, pending_event, "paused: coalesced event fired on resume")
            stored = tx.get(EVENTS_COLLECTION, pending_event)
            if stored is None:
                continue
            self._decide(
                tx, stored["envelope"], rules, {rule.id}, marker_id, placed=placed, chained=False
            )

    def _admit(
        self,
        tx: StoreOps,
        rule: Rule,
        rules: list[Rule],
        envelope: Mapping[str, Any],
        decision: Decision,
        run_id: str,
        intent_id: str,
    ) -> tuple[Decision, str | None]:
        """Reserve ``rule``'s concurrency key for a firing ``decision``: the decision (a
        skip in its place when the key is held, the budget spent or the key unresolved)
        and the resolved key. The budget is the smallest ``max_attempts`` among the live
        rules whose template resolves to the same key on this event (module doc)."""
        try:
            key = resolve_concurrency_key(rule.concurrency_key or "", envelope)
        except ValueError as exc:
            # Fail closed for this rule only: never fire unprotected, never wedge the feed.
            skip = Decision(
                rule_id=rule.id, fire=False, reason=CONCURRENCY_KEY_UNRESOLVED, detail=str(exc)
            )
            return skip, None
        limit = _shared_max_attempts(rules, envelope, key)
        # Read before reserving (same transaction): an admission clears the key's pending
        # event and a deduplication replaces it - either way it never fires (module doc).
        displaced = _pending(tx, key)
        reason = reserve_concurrency(tx, rule.id, key, run_id, intent_id, limit)
        if reason in (None, DEDUPLICATED):
            _coalesce_away(tx, displaced, rule.id, envelope["id"])
        if reason is None:
            return decision, key
        detail = key
        if reason == DEDUPLICATED:
            holder = note_deduplicated(tx, rule.id, key, envelope["id"])
            detail = f"{key}: held by run {holder}"
        return Decision(rule_id=rule.id, fire=False, reason=reason, detail=detail), key

    def _facts(
        self, tx: StoreOps, rules: list[Rule], event_id: str
    ) -> tuple[RunFacts, dict[str, str]]:
        """Run facts and predecessor states for ``event_id``, for every predecessor rule."""
        outcomes: dict[str, RuleOutcome] = {}
        states: dict[str, str] = {}
        for pid in sorted({p for r in rules for p in (*r.must_after, *r.may_after)}):
            run = tx.get(RUNS_COLLECTION, run_id_for(pid, event_id))
            if run is not None:
                states[pid] = run.get("status") or LIVE
                outcomes[pid] = RuleOutcome(status=states[pid], outputs=run.get("outputs") or {})
                continue
            intent = tx.get(RULE_FIRES, firing_key(pid, event_id))
            if intent is not None:
                states[pid] = START_FAILED if intent.get("status") == "failed" else LIVE
        return RunFacts(outcomes=outcomes), states

    def _decide(
        self,
        tx: StoreOps,
        envelope: Mapping[str, Any],
        rules: list[Rule],
        ours: set[str],
        marker_id: str,
        *,
        placed: bool,
        chained: bool,
    ) -> None:
        event_id = envelope["id"]
        workflows = {
            w.id: w for w in (Workflow.from_dict(d, strict=False) for d in tx.find("workflows"))
        }
        facts, states = self._facts(tx, rules, event_id)
        now = self._clock()
        refs = {r.id: rule_variable_refs(r) for r in rules}
        wanted = set().union(*refs.values()) if self.variables else set()
        values = variable_values(tx, wanted) if wanted else {}
        decisions = match(
            envelope,
            rules,
            facts,
            workflows=workflows,
            paused=is_paused(tx),
            variables=values,
            variables_supported=self.variables,
            trigger_match=_trigger_matcher(envelope, rules),
        )
        decisions = self._rate_capped(tx, decisions, rules, ours, event_id, now)
        decisions = _admission_settled(tx, decisions, rules, ours, event_id)
        by_id = {r.id: r for r in rules}
        for decision in sequence(decisions, rules, states):
            if decision.rule_id not in ours:
                continue
            intent_id = firing_key(decision.rule_id, event_id)
            if tx.get(RULE_FIRES, intent_id) is not None:
                continue  # already fired for this event (by another host, or another path)
            self._pending[marker_id].append((decision.rule_id, event_id))
            run_id = run_id_for(decision.rule_id, event_id)
            key = None
            if decision.fire and by_id[decision.rule_id].concurrency_key is not None:
                decision, key = self._admit(
                    tx, by_id[decision.rule_id], rules, envelope, decision, run_id, intent_id
                )
            # A skip that matters ("superseded by A", ...) is part of the rule's history;
            # it commits (or rolls back) with this transaction, once per (rule, event). A
            # waiting record is superseded by the outcome once the predecessor settles.
            settle_decision(
                tx,
                decision,
                event_id=event_id,
                host=self.host,
                at=utc_timestamp(now),
                run_id=run_id,
                always=chained,
            )
            if decision.fire:
                _count_fire(tx, by_id[decision.rule_id], now)
                snapshot = {n: values[n] for n in sorted(refs[decision.rule_id])}
                tx.insert(
                    RULE_FIRES,
                    {
                        **({"variables": snapshot} if snapshot else {}),
                        **({"concurrency_key": key} if key is not None else {}),
                        "id": intent_id,
                        "rule_id": decision.rule_id,
                        "event_id": event_id,
                        "run_id": run_id,
                        "host": self.host,
                        "placed": placed,
                        "fired_at": utc_timestamp(now),
                        "status": "pending",
                        "trigger": dict(envelope),
                        "upstream": {k: dict(v) for k, v in decision.upstream.items()},
                    },
                )

    def _rate_capped(
        self,
        tx: StoreOps,
        decisions: tuple[Decision, ...],
        rules: list[Rule],
        ours: set[str],
        event_id: str,
        now: datetime,
    ) -> tuple[Decision, ...]:
        """``decisions`` with every fire over its rule's cap turned into a ``rate_capped``
        skip (module doc, "Rate cap"). Only this consumer's rules are checked against
        their window; a predecessor that is somebody else's is capped only once its own
        host recorded it so (sticky), never on a guess."""
        predecessors = {p for r in rules for p in (*r.must_after, *r.may_after)}
        by_id = {r.id: r for r in rules}
        out: list[Decision] = []
        for d in decisions:
            rid = d.rule_id
            if d.fire and rid in by_id and (rid in ours or rid in predecessors):
                d = _cap(tx, d, by_id[rid], event_id, now, mine=rid in ours)
            out.append(d)
        return tuple(out)

    # ------------------------------------------------------------------ starting

    def start_fired(self) -> list[str]:
        """Turn pending intents into runs; return the run ids this host started."""
        started: list[str] = []
        for intent in self.store.find(RULE_FIRES, {"status": "pending"}):
            if intent.get("placed") and intent.get("host") != self.host:
                self._abandon_if_host_gone(intent)  # else it starts where it was evaluated
                continue
            with log_context(run_id=intent["run_id"], host=self.host):
                if self._unresolved(intent):
                    continue
                try:
                    self.executor.start_from_store(
                        intent["rule_id"],
                        trigger=intent["trigger"],
                        upstream=intent.get("upstream") or None,
                        run_id=intent["run_id"],
                        variables=intent.get("variables"),
                        concurrency_key=intent.get("concurrency_key"),
                        fence=_claim_intent(intent),
                    )
                except _IntentGone as gone:
                    log.info("rule %s on event %s not started: %s", *_ids(intent), gone)
                    continue  # abandoned, started elsewhere, or its reservation is gone
                except TransientStoreError:
                    # A write conflict on the intent: someone else moved it (an abandonment
                    # or another host's start) first. The next cycle re-reads it.
                    log.info("rule %s on event %s: start conflicted, re-read", *_ids(intent))
                    continue
                except DuplicateKeyError:
                    pass  # another host started it first
                except RunError as exc:
                    if exc.code == "paused":
                        continue
                    log.warning("rule %s did not start: %s", intent["rule_id"], exc.code)
                    self.store.update_if(
                        RULE_FIRES,
                        intent["id"],
                        {"status": "pending"},
                        {"status": "failed", "error": exc.code},
                    )
                    continue
                else:
                    started.append(intent["run_id"])
                    log.info("run started for rule %s on event %s", *_ids(intent))
                self.store.update_if(
                    RULE_FIRES, intent["id"], {"status": "pending"}, {"status": "started"}
                )
        return started

    def _abandon_if_host_gone(self, intent: Mapping[str, Any]) -> None:
        """Fail another host's pending placed intent once that host has been offline for
        :data:`~culture_rules.engine.runs.PLACEMENT_ABANDON_AFTER` (module doc, "Stranded
        placed intents"); a host that still beats, however slow, keeps it."""
        now = self._clock()
        host = intent.get("host") or ""
        beat = self.store.get(HEARTBEAT_COLLECTION, host)
        if beat is not None:
            last = _moment(beat.get("ts"))
            if last is None or now - last < PLACEMENT_ABANDON_AFTER:
                return  # beating (or an unreadable beat: no proof of death)
        else:
            fired = _moment(intent.get("fired_at"))
            if fired is not None and now - fired < PLACEMENT_ABANDON_AFTER:
                return  # never beat: count from the firing instead
        if self.store.get(RUNS_COLLECTION, intent["run_id"]) is not None:
            # The host started the run, then died before marking the intent: it is live.
            self.store.update_if(
                RULE_FIRES, intent["id"], {"status": "pending"}, {"status": "started"}
            )
            return
        outcome = self.store.update_if(
            RULE_FIRES,
            intent["id"],
            {"status": "pending"},
            {
                "status": "failed",
                "error": PLACEMENT_UNAVAILABLE,
                "abandoned_by": self.host,
                "abandoned_at": utc_timestamp(now),
            },
        )
        if outcome.won:
            log.warning(
                "rule %s on event %s abandoned: host %s offline past %s",
                *_ids(intent),
                host,
                PLACEMENT_ABANDON_AFTER,
            )

    def _unresolved(self, intent: Mapping[str, Any]) -> bool:
        """Refuse (mark failed) an intent for a variable-referencing rule that carries no
        variable snapshot: a node that did not resolve variables fired it (module doc)."""
        if "variables" in intent:
            return False
        rule = self.store.get("rules", intent["rule_id"])
        if rule is None or not rule_variable_refs(rule):
            return False
        log.warning(
            "rule %s on event %s not started: fired without shared variables resolved",
            *_ids(intent),
        )
        self.store.update_if(
            RULE_FIRES,
            intent["id"],
            {"status": "pending"},
            {"status": "failed", "error": VARIABLES_UNSUPPORTED},
        )
        return True


def _trigger_matcher(envelope: Mapping[str, Any], rules: list[Rule]) -> TriggerMatcher:
    """The trigger test for ``envelope``: a schedule or probe event
    (:mod:`culture_rules.node.schedule`, :mod:`culture_rules.node.probe_trigger`) targets one
    rule, so only the trigger of the rule named by its ``data.rule_id`` matches it - every
    other rule of that kind (no ``type``) would match it otherwise."""
    if envelope.get("kind") not in ("schedule", "probe"):
        return trigger_matches
    target = (envelope.get("data") or {}).get("rule_id")
    targets = [r.trigger for r in rules if r.id == target]
    # Identity, not equality: two rules may carry equal triggers (same cron).
    return lambda trigger, event: any(trigger is t for t in targets) and trigger_matches(
        trigger, event
    )


BUDGET_RESET_FAMILIES = ("github.",)
"""Trigger types whose rules a budget-reset event concerns: GitHub events (the pr-fixer's
``checks_settled``, comment and review triggers alike); the key must also resolve."""


def _resets_budgets(envelope: Mapping[str, Any]) -> bool:
    """Whether ``envelope`` is an explicit attempt-budget reset signal: a push not by this
    node's own identity (``self_authored`` explicitly false; an absent tag is never read as
    human), or checks settled green (module doc, "Concurrency keys")."""
    data = envelope.get("data")
    if not isinstance(data, Mapping):
        return False
    kind = envelope.get("type")
    if kind == "github.pr.synchronize":
        return data.get("self_authored") is False
    return kind == "github.pr.checks_settled" and data.get("conclusion") == "success"


def _budget_reset_applies(rule: Rule) -> bool:
    """Whether a reset event concerns ``rule``: a keyed rule triggered by a GitHub event
    (whatever its exact type: a fixer triggered by settled checks or a comment is reset by
    a human push). Its key must also resolve on the event."""
    wanted = rule.trigger.params.get("type")
    return (
        rule.concurrency_key is not None
        and rule.trigger.kind == "event"
        and isinstance(wanted, str)
        and wanted.startswith(BUDGET_RESET_FAMILIES)
    )


def _resolved_key(rule: Rule, envelope: Mapping[str, Any]) -> str | None:
    try:
        return resolve_concurrency_key(rule.concurrency_key or "", envelope)
    except ValueError:
        return None


def _reset_keys(rules: list[Rule], ours: set[str], envelope: Mapping[str, Any]) -> set[str]:
    """The distinct keys a reset event resets: those this consumer's concerned rules resolve
    (a rule whose key does not resolve on it is skipped, never wedging the feed)."""
    keys = {_resolved_key(r, envelope) for r in rules if r.id in ours and _budget_reset_applies(r)}
    return {k for k in keys if k is not None}


def _shared_max_attempts(rules: list[Rule], envelope: Mapping[str, Any], key: str) -> int | None:
    """The attempt budget of ``key``: the smallest ``max_attempts`` declared by a live rule
    whose template resolves to ``key`` on ``envelope`` (``None``: no rule sets one)."""
    limits = [
        r.max_attempts
        for r in rules
        if r.concurrency_key is not None
        and r.max_attempts is not None
        and _resolved_key(r, envelope) == key
    ]
    return min(limits) if limits else None


ADMISSION_FINAL = (ATTEMPT_BUDGET_EXHAUSTED, CONCURRENCY_KEY_UNRESOLVED)
"""Admission skips that are final for their (rule, event): no later evaluation admits it."""


def _admission_final(record: Mapping[str, Any]) -> bool:
    """Whether a decision record is a final admission skip: the budget was spent or the key
    did not resolve, or the event was deduplicated and then replaced as its key's newest
    deduplicated event (``coalesced``: it will never fire). A deduplicated event that is
    still its key's pending one is not final - it fires once the holding run ends."""
    reason = record.get("reason")
    return reason in ADMISSION_FINAL or (reason == DEDUPLICATED and bool(record.get("coalesced")))


def _admission_settled(
    tx: StoreOps,
    decisions: tuple[Decision, ...],
    rules: list[Rule],
    ours: set[str],
    event_id: str,
) -> tuple[Decision, ...]:
    """``decisions`` with every fire whose (rule, event) already recorded a final admission
    skip turned back into that skip, *before* sequencing (module doc, "Concurrency keys").

    Admission runs after :func:`sequence`, so a chain re-evaluation would otherwise rebuild
    the predecessor as eligible from matching alone and keep its dependant waiting forever;
    with it, a must-after dependant settles as ``predecessor_failed`` and a may-after one
    fires without it. Sticky, like ``rate_capped``: never re-admitted for that event."""
    predecessors = {p for r in rules for p in (*r.must_after, *r.may_after)}
    out: list[Decision] = []
    for d in decisions:
        rid = d.rule_id
        if d.fire and (rid in ours or rid in predecessors):
            record = tx.get(RULE_DECISIONS, decision_key(rid, event_id))
            if record is not None and _admission_final(record):
                d = Decision(
                    rule_id=rid,
                    fire=False,
                    reason=record["reason"],
                    detail=record.get("detail") or "",
                )
        out.append(d)
    return tuple(out)


def _pending(tx: StoreOps, key: str) -> tuple[str, str] | None:
    """The key's pending deduplicated event as ``(event_id, rule_id)``, if any."""
    budget = tx.get(RULE_ATTEMPT_BUDGETS, budget_id(key)) or {}
    event, rule = budget.get("pending_event_id"), budget.get("pending_rule_id")
    return (event, rule) if event and rule else None


def _coalesce_away(
    tx: StoreOps, displaced: tuple[str, str] | None, rule_id: str, event_id: str
) -> None:
    """``event_id`` (via ``rule_id``) displaced the key's pending deduplicated event - it was
    admitted (the reservation clears the slot: the holder ended but its completion was not
    handled yet) or deduplicated in its place: mark the displaced record ``coalesced``,
    final - it will never fire, the holder's completion finds the key held by another run
    - so the chain feed settles its dependants (:func:`_settled_skip`)."""
    if displaced is None or displaced == (event_id, rule_id):
        return
    event, rule = displaced
    tx.update_if(
        RULE_DECISIONS, decision_key(rule, event), {"reason": DEDUPLICATED}, {"coalesced": True}
    )


def _capped(rule_id: str, detail: str) -> Decision:
    return Decision(rule_id=rule_id, fire=False, reason=RATE_CAPPED, detail=detail)


def _cap(
    tx: StoreOps, d: Decision, rule: Rule, event_id: str, now: datetime, *, mine: bool
) -> Decision:
    """``d`` (a fire of ``rule``), or a ``rate_capped`` skip in its place."""
    record = tx.get(RULE_DECISIONS, decision_key(rule.id, event_id))
    if record is not None and record.get("reason") == RATE_CAPPED:
        return _capped(rule.id, record.get("detail") or "")  # sticky per (rule, event)
    if not mine or tx.get(RULE_FIRES, firing_key(rule.id, event_id)) is not None:
        return d  # not ours to decide, or already fired for this event
    cap = max_fires_per_hour(rule)
    if cap is None:
        return d  # uncapped (a schedule rule without an explicit cap)
    recent = _recent_fires(tx.get(RULE_RATES, rule.id), now)
    if len(recent) < cap:
        return d
    return _capped(rule.id, f"{len(recent)} fires in the last hour (cap {cap})")


def _recent_fires(window: Mapping[str, Any] | None, now: datetime) -> list[str]:
    """The fire timestamps of ``window`` still inside the trailing hour before ``now``."""
    since = _aware(now) - RATE_WINDOW
    recent: list[str] = []
    for at in (window or {}).get("fires") or ():
        try:
            moment = _aware(datetime.fromisoformat(at))
        except (TypeError, ValueError):
            continue  # not a timestamp: ignore it (the next write prunes it)
        if moment > since:
            recent.append(at)
    return recent


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _count_fire(tx: StoreOps, rule: Rule, now: datetime) -> None:
    """Add a fire of ``rule`` at ``now`` to its window (the cross-host serialisation point)."""
    cap = max_fires_per_hour(rule)
    if cap is None:
        return  # uncapped: no window to keep
    recent = _recent_fires(tx.get(RULE_RATES, rule.id), now)
    fires = [*recent, utc_timestamp(now)][-cap:]
    tx.put(RULE_RATES, {"id": rule.id, "rule_id": rule.id, "fires": fires})


class _IntentGone(Exception):
    """A firing intent's start was fenced off: it is no longer pending, or no longer holds
    its concurrency reservation."""


def _claim_intent(intent: Mapping[str, Any]) -> Callable[[StoreOps], None]:
    """The fence a pending intent's run insert runs in (module doc, "Stranded placed
    intents"): in the insert's own transaction, the intent must still be ``pending`` and -
    keyed - still the key's reservation holder, and it moves to ``started`` there. So the
    start and an abandonment (``pending`` -> ``failed`` by compare-and-set) never both
    commit: whichever writes the intent first wins, the other's write fails or conflicts."""

    def fence(tx: StoreOps) -> None:
        current = tx.get(RULE_FIRES, intent["id"])
        if current is None or current.get("status") != "pending":
            raise _IntentGone(f"intent is {(current or {}).get('status', 'gone')}")
        key = intent.get("concurrency_key")
        if key is not None:
            budget = tx.get(RULE_ATTEMPT_BUDGETS, budget_id(key)) or {}
            if budget.get("intent_id") != intent["id"]:
                raise _IntentGone("the concurrency reservation is held by another firing")
        moved = tx.update_if(RULE_FIRES, intent["id"], {"status": "pending"}, {"status": "started"})
        if not moved.won:
            raise _IntentGone("intent changed while starting")

    return fence


def _moment(text: Any) -> datetime | None:
    """An aware datetime from a stored ISO timestamp, else None."""
    try:
        return _aware(datetime.fromisoformat(str(text)))
    except ValueError:
        return None


def _ids(intent: Mapping[str, Any]) -> tuple[str, str]:
    return intent["rule_id"], intent["event_id"]


def _finished_run(doc: Mapping[str, Any]) -> str | None:
    """A run of a rule fired on an event that reached a terminal state (its id), else None."""
    if doc.get("status") not in RUN_DONE:
        return None
    rule_id = (doc.get("rule") or {}).get("id")
    event_id = (doc.get("trigger") or {}).get("id")
    if not rule_id or not event_id or doc.get("id") != run_id_for(rule_id, event_id):
        return None  # started by hand, not by an event: no chain to continue
    return doc["id"]


def _settled_skip(doc: Mapping[str, Any]) -> str | None:
    """A final skip decision (its id), else None: a waiting decision settled as a skip, or
    a recorded skip written final in one step (no ``superseded`` history - e.g. a
    ``predecessor_failed`` decided on another host after the predecessor finished, or a
    ``rate_capped``). Each is handled once per consumer (the per-key marker)."""
    if doc.get("fire"):
        return None
    reason = doc.get("reason")
    if reason in (BLOCKED_BY_PREDECESSOR, FIRE):
        return None
    if reason == DEDUPLICATED:
        # Not final while it is its key's newest deduplicated event (it may still fire);
        # final once a newer one replaced it (its own marker key: the record may have
        # settled a waiting state before).
        return f"{doc.get('id')}/coalesced" if doc.get("coalesced") else None
    if not doc.get("superseded") and reason not in FINAL_SKIP_REASONS:
        return None
    return doc.get("id")


def _failed_intent(doc: Mapping[str, Any]) -> str | None:
    """A firing intent whose run could not start (its id), else None."""
    return doc.get("id") if doc.get("status") == "failed" else None
