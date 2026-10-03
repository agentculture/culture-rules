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
history can say "superseded by A". Standard-library only.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from culture_rules.engine.claims import firing_key
from culture_rules.engine.decisions import RULE_DECISIONS, record_decision
from culture_rules.engine.matching import match
from culture_rules.engine.placement import MachineState, Resolved, resolve_rule_placement
from culture_rules.engine.runs import (
    FATAL_PLACEMENT,
    Executor,
    RunError,
    drained_machines,
    is_paused,
)
from culture_rules.events.triggers import FIRES_COLLECTION, EventTriggers
from culture_rules.machines.enrol import enrolled_machines
from culture_rules.machines.heartbeat import online_machines
from culture_rules.model.actor import Actor
from culture_rules.model.rule import Rule
from culture_rules.model.workflow import Workflow
from culture_rules.ops.logs import log_context
from culture_rules.store.port import DuplicateKeyError, StoragePort, StoreOps
from culture_rules.store.versioning import utc_timestamp

__all__ = [
    "RULE_FIRES",
    "SHARED_CONSUMER",
    "Deferred",
    "RuleFiring",
    "placed_consumer",
    "run_id_for",
]

log = logging.getLogger("culture_rules.node.firing")

RULE_FIRES = "rule_fires"
"""Firing intents: one per (rule, event) that matched, committed with the trigger fire."""
SHARED_CONSUMER = "triggers"
"""The trigger consumer every host shares for unplaced rules."""


def placed_consumer(host: str) -> str:
    """The per-host trigger consumer for rules placed on ``host``."""
    return f"triggers@{host}"


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
    ) -> None:
        self.store = store
        self.host = host
        self.executor = executor
        self._clock = clock
        self._pending: dict[str, list[str]] = {}
        self.placed = EventTriggers(
            store,
            lambda tx, ev: self._evaluate(tx, ev, placed=True),
            host=host,
            consumer=placed_consumer(host),
            handler_collections=(RULE_FIRES, RULE_DECISIONS),
            clock=clock,
        )
        self.shared = EventTriggers(
            store,
            lambda tx, ev: self._evaluate(tx, ev, placed=False),
            host=host,
            consumer=SHARED_CONSUMER,
            handler_collections=(RULE_FIRES, RULE_DECISIONS),
            clock=clock,
        )

    # ------------------------------------------------------------------ polling

    def poll(self, consumer: EventTriggers) -> PollOutcome:
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
            for event_id, rule_ids in self._pending.items():
                marker = self.store.get(FIRES_COLLECTION, consumer.fire_id(event_id))
                if marker is not None and marker.get("host") == self.host:
                    outcome.evaluated += [(rule_id, event_id) for rule_id in rule_ids]
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

    def _evaluate(self, tx: StoreOps, event: Mapping[str, Any], *, placed: bool) -> None:
        envelope = event["envelope"]
        event_id = envelope["id"]
        self._pending[event_id] = []  # a retried transaction re-evaluates from scratch
        rules = [
            Rule.from_dict(d, strict=False) for d in tx.find("rules") if not d.get("deleted_at")
        ]
        if placed:
            placed_rules = [r for r in rules if r.placement is not None]
            if not placed_rules:
                return
            snapshot = self._snapshot(tx)
            ours = {r.id for r in placed_rules if self._mine(r, snapshot, event_id)}
        else:
            ours = {r.id for r in rules if r.placement is None}
        if not ours:
            return
        workflows = {
            w.id: w for w in (Workflow.from_dict(d, strict=False) for d in tx.find("workflows"))
        }
        for decision in match(envelope, rules, workflows=workflows, paused=is_paused(tx)):
            if decision.rule_id not in ours:
                continue
            intent_id = firing_key(decision.rule_id, event_id)
            if tx.get(RULE_FIRES, intent_id) is not None:
                continue  # already fired for this event (by another host)
            self._pending[event_id].append(decision.rule_id)
            # A skip that matters ("superseded by A", ...) is part of the rule's history;
            # it commits (or rolls back) with this transaction, once per (rule, event).
            record_decision(
                tx, decision, event_id=event_id, host=self.host, at=utc_timestamp(self._clock())
            )
            if decision.fire:
                tx.insert(
                    RULE_FIRES,
                    {
                        "id": intent_id,
                        "rule_id": decision.rule_id,
                        "event_id": event_id,
                        "run_id": run_id_for(decision.rule_id, event_id),
                        "host": self.host,
                        "placed": placed,
                        "status": "pending",
                        "trigger": dict(envelope),
                        "upstream": {k: dict(v) for k, v in decision.upstream.items()},
                    },
                )

    # ------------------------------------------------------------------ starting

    def start_fired(self) -> list[str]:
        """Turn pending intents into runs; return the run ids this host started."""
        started: list[str] = []
        for intent in self.store.find(RULE_FIRES, {"status": "pending"}):
            if intent.get("placed") and intent.get("host") != self.host:
                continue  # a placed rule's run starts where it was evaluated
            with log_context(run_id=intent["run_id"], host=self.host):
                try:
                    self.executor.start_from_store(
                        intent["rule_id"],
                        trigger=intent["trigger"],
                        upstream=intent.get("upstream") or None,
                        run_id=intent["run_id"],
                    )
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


def _ids(intent: Mapping[str, Any]) -> tuple[str, str]:
    return intent["rule_id"], intent["event_id"]
