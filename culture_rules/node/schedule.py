"""Schedule triggers: a rule with ``trigger.kind == "schedule"`` fires once per cron slot.

A node's scheduler stage (:meth:`Scheduler.tick`, run by
:meth:`~culture_rules.node.daemon.Node.run_once` before the trigger consumers are polled)
turns every cron slot that came due since its previous tick into a **schedule event** in
the ``events`` collection. From there the slot travels the ordinary event path of
:mod:`culture_rules.node.firing`: the placed/shared trigger consumers evaluate it, commit a
``rule_fires`` intent and start one run whose id is derived from ``(rule, event)``. The
scheduler adds no second decision path; it only decides *which slots exist* and *who
synthesizes them*.

The schedule event
==================
One per ``(rule, slot)``; ``slot`` is the slot's instant as an ISO-8601 UTC string::

    {"id": "schedule/<rule id>/<slot>", "kind": "schedule", "type": "schedule",
     "source": "culture-rules/schedule", "time": <slot>,
     "data": {"rule_id": ..., "slot": ..., "cron": ..., "tz": ...}}

A schedule event targets exactly one rule: matching (see ``_trigger_matcher`` in
:mod:`culture_rules.node.firing`) lets only the rule named by ``data.rule_id`` match it, so
two schedule rules never fire on each other's slots and never supersede each other.

Exactly once
============
The event id **is** the ``rule/slot`` marker. It is inserted in a store transaction that
first looks it up; a duplicate key (or an existing document) means another process -
another host racing an unplaced rule's slot, a second process on the placed host, or this
host before a restart - already synthesized that slot, and nothing more happens. One event
then fires at most one run (the trigger fire marker, the intent id and the run id all
derive from it), so a slot fires at most once mesh-wide.

Who synthesizes
===============
* a **placed** rule only on the host its placement resolves to (the same resolution the
  placed trigger consumer applies, :meth:`RuleFiring._mine`); a host that would own it but
  is drained or offline synthesizes nothing - the slot is not kept for later;
* an **unplaced** rule on every host; the marker makes the first one win.

No backfill
===========
The window is in memory, per process: the first tick (at :meth:`Node.start`) only records
``now``; every later tick considers the slots in ``(previous tick, now]``. A slot that came
due while the node was down is never fired later, and a restarted node starts a fresh
window at its own start time. A tick that fails (a store error) keeps its window start, so
the next tick retries those slots - the markers absorb any slot that did commit.

Disabled or deleted rules synthesize nothing; a rule whose ``cron``/``tz`` does not parse
is logged and skipped. Standard-library only.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from culture_rules.engine.cron import parse
from culture_rules.events.ingest import EVENTS_COLLECTION, event_document
from culture_rules.model.rule import Rule
from culture_rules.node.firing import Deferred, RuleFiring
from culture_rules.store.port import DuplicateKeyError, StoragePort, StoreOps
from culture_rules.store.retry import run_transaction

__all__ = ["SCHEDULE_KIND", "SCHEDULE_SOURCE", "Scheduler", "schedule_event_id", "slot_key"]

log = logging.getLogger("culture_rules.node.schedule")

SCHEDULE_KIND = "schedule"
"""Trigger kind and event kind of schedule events."""
SCHEDULE_SOURCE = "culture-rules/schedule"
"""The ``source`` of a synthesized schedule event."""

_EPSILON = timedelta(microseconds=1)


def slot_key(slot: datetime) -> str:
    """A slot's marker form: its instant as an ISO-8601 UTC string (seconds precision)."""
    return slot.astimezone(UTC).isoformat(timespec="seconds")


def schedule_event_id(rule_id: str, slot: str) -> str:
    """The schedule event (and marker) id of ``rule_id`` at ``slot`` (a :func:`slot_key`)."""
    return f"schedule/{rule_id}/{slot}"


class Scheduler:
    """One process's schedule stage (see the module docstring)."""

    def __init__(
        self,
        store: StoragePort,
        host: str,
        firing: RuleFiring,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self.store = store
        self.host = host
        self.firing = firing
        self._clock = clock
        self.last_tick: datetime | None = None
        """End of the previous window (in memory: no backfill across restarts)."""

    def tick(self) -> list[str]:
        """Synthesize the schedule events due in ``(last tick, now]``; return their ids."""
        now = self._clock()
        start = self.last_tick
        if start is None or now <= start:
            if start is None:
                self.last_tick = now  # startup: nothing before this instant fires
            return []
        synthesized: list[str] = []
        snapshot = functools.cache(lambda: self.firing._snapshot(self.store))  # loaded once
        for rule in self._schedule_rules():
            for slot in self._due_here(rule, start, now, snapshot):
                if self._synthesize(rule, slot):
                    synthesized.append(schedule_event_id(rule.id, slot))
        self.last_tick = now  # only once every due slot is committed (or already was)
        return synthesized

    # ------------------------------------------------------------------ helpers

    def _due_here(
        self, rule: Rule, start: datetime, now: datetime, snapshot: Callable[[], tuple]
    ) -> list[str]:
        """The due slots of ``rule`` when this node synthesizes them (else none)."""
        slots = self._slots(rule, start, now)
        if not slots:
            return []
        if rule.placement is not None and not self._placed_here(rule, snapshot(), slots[0]):
            return []
        return slots

    def _schedule_rules(self) -> list[Rule]:
        rules = []
        for doc in self.store.find("rules"):
            if doc.get("deleted_at") or (doc.get("trigger") or {}).get("kind") != SCHEDULE_KIND:
                continue
            try:
                rule = Rule.from_dict(doc, strict=False)
            except ValueError as exc:  # ModelParseError: one bad doc must not stop the others
                log.warning("schedule rule %s skipped: unparseable: %s", doc.get("id"), exc)
                continue
            if rule.enabled:
                rules.append(rule)
        return sorted(rules, key=lambda r: r.id)

    def _slots(self, rule: Rule, start: datetime, now: datetime) -> list[str]:
        params = rule.trigger.params
        try:
            cron = parse(params["cron"])
            due = cron.slots_between(start, now + _EPSILON, params.get("tz"))
            return [slot_key(s) for s in due if s > start]
        except (KeyError, TypeError, ValueError) as exc:  # ZoneInfoNotFoundError is a KeyError
            log.warning("schedule rule %s skipped: bad cron/tz: %s", rule.id, exc)
            return []

    def _placed_here(self, rule: Rule, snapshot: tuple, slot: str) -> bool:
        try:
            return self.firing._mine(rule, snapshot, schedule_event_id(rule.id, slot))
        except Deferred as deferred:  # ours, but drained/offline: the slot is not kept
            log.info("schedule %s", deferred)
            return False

    def _synthesize(self, rule: Rule, slot: str) -> bool:
        """Insert the schedule event for ``(rule, slot)``; False when it already exists."""
        event_id = schedule_event_id(rule.id, slot)
        envelope = {
            "id": event_id,
            "kind": SCHEDULE_KIND,
            "type": SCHEDULE_KIND,
            "source": SCHEDULE_SOURCE,
            "time": slot,
            "data": {
                "rule_id": rule.id,
                "slot": slot,
                "cron": rule.trigger.params.get("cron"),
                "tz": rule.trigger.params.get("tz"),
            },
        }

        def insert(tx: StoreOps) -> bool:
            if tx.get(EVENTS_COLLECTION, event_id) is not None:
                return False
            tx.insert(
                EVENTS_COLLECTION,
                event_document(envelope, host=self.host, received_at=self._clock()),
            )
            return True

        try:
            created = run_transaction(self.store, insert)
        except DuplicateKeyError:
            created = False  # another process committed the same slot first
        if created:
            log.info("schedule slot %s of rule %s synthesized", slot, rule.id)
        return created
