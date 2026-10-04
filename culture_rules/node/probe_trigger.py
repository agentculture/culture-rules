"""Probe triggers: run an allow-listed command on a schedule; fire on change or on a condition.

A rule with ``trigger.kind == "probe"`` names a *runner* actor (``params.actor``), one of
its registered commands (``params.command``, with optional ``params.args``), a cron
``params.schedule`` and a ``params.mode``. The probe stage
(:meth:`ProbeTrigger.tick`, run by :meth:`~culture_rules.node.daemon.Node.run_once` right
after the scheduler) runs the command once per cron slot that came due and turns a
successful run into a **probe event** in the ``events`` collection. From there the event
travels the ordinary path of :mod:`culture_rules.node.firing`; the probe adds no second
decision path.

Running the command
===================
Through the actor's :class:`~culture_rules.actors.code.CodeRunner` (argv template, typed
args, the inline-eval guard, its own timeout, a sandbox directory, ``shell=False``). The
command runs on the machine that owns the actor: this host handles a probe rule only when
the actor's ``machine`` is this host. An actor without a machine falls back to the rule's
placement (the scheduler's resolution, ``RuleFiring._mine``); with neither, every host
tries and the slot marker lets the first win. Disabled rules and disabled or missing
actors run nothing. A run that does not complete (a nonzero exit, a timeout, an unknown
command) emits nothing and records nothing: only the outcome is logged, never the output.

The probe event
===============
One per ``(rule, slot)``; the id is the marker::

    {"id": "probe/<rule id>/<slot>", "kind": "probe", "type": "probe",
     "source": "culture-rules/probe", "time": <slot>,
     "data": {<JSON object output keys...>, "stdout": <truncated>, "exit_code": 0,
              "rule_id": ..., "slot": ...}}

When stdout parses as a JSON object its keys become event data (so a condition can read
``trigger.data.temp``); the reserved keys above always win. Matching lets only the rule
named by ``data.rule_id`` match it (``_trigger_matcher`` in :mod:`culture_rules.node.firing`).

Modes
=====
* ``change`` - a stable digest of the output is compared with the one persisted per rule in
  the ``probe_state`` collection; the event is emitted (and the digest recorded, in the same
  transaction) only when it differs. The first run always emits.
* ``condition`` - every successful run is emitted; the rule's own ``condition`` (evaluated
  against the event envelope as ``trigger``) decides whether it fires.

Exactly once
============
The marker is inserted in a store transaction that first looks it up; a duplicate means
another process (or this host before a restart) already handled that slot. The window is
in memory with no backfill, exactly as for :class:`~culture_rules.node.schedule.Scheduler`;
a tick that fails on a store error keeps its window start and retries. Standard-library
only.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from culture_rules.actors.code import CodeRunner
from culture_rules.engine.actorport import COMPLETED, InvocationContext, InvocationResult
from culture_rules.engine.cron import parse
from culture_rules.events.ingest import EVENTS_COLLECTION, event_document
from culture_rules.model.actor import Actor
from culture_rules.model.rule import Rule
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.node.firing import Deferred, RuleFiring
from culture_rules.node.schedule import slot_key
from culture_rules.store.port import DuplicateKeyError, StoragePort, StoreOps
from culture_rules.store.retry import run_transaction

__all__ = [
    "PROBE_KIND",
    "PROBE_SOURCE",
    "PROBE_STATE",
    "CommandRunner",
    "ProbeTrigger",
    "probe_event_id",
]

log = logging.getLogger("culture_rules.node.probe_trigger")

PROBE_KIND = "probe"
PROBE_SOURCE = "culture-rules/probe"
PROBE_STATE = "probe_state"
"""Collection holding the previous output digest per probe rule (``change`` mode)."""
MAX_STDOUT = 4096
"""Longest ``data.stdout`` carried on the event (the digest covers the full output)."""

CommandRunner = Callable[[Actor, str, Mapping[str, Any], str], InvocationResult]
"""Runs ``command`` with ``args`` on ``actor``'s runner; the last argument is a unique key."""

_EPSILON = timedelta(microseconds=1)
_RESERVED = ("stdout", "exit_code", "rule_id", "slot")


def probe_event_id(rule_id: str, slot: str) -> str:
    """The probe event (and marker) id of ``rule_id`` at ``slot`` (a ``slot_key``)."""
    return f"probe/{rule_id}/{slot}"


def default_runner(
    actor: Actor, command: str, args: Mapping[str, Any], key: str, *, host: str = ""
) -> InvocationResult:
    """The production runner: the actor's registered commands through a CodeRunner."""
    runner = CodeRunner(actor.params.get("commands") or {}, is_admin=lambda _identity: False)
    context = InvocationContext(run_id=key, step_id="probe", kind="code", host=host, actor=actor.id)
    deadline = datetime.now(UTC) + timedelta(hours=1)  # advisory; the command timeout bounds it
    return runner.invoke({"command": command, "args": dict(args)}, key, deadline, context=context)


class ProbeTrigger:
    """One process's probe stage (see the module docstring)."""

    def __init__(
        self,
        store: StoragePort,
        host: str,
        firing: RuleFiring,
        *,
        clock: Callable[[], datetime],
        runner: CommandRunner | None = None,
    ) -> None:
        self.store = store
        self.host = host
        self.firing = firing
        self._clock = clock
        self._runner = runner or (
            lambda actor, command, args, key: default_runner(
                actor, command, args, key, host=self.host
            )
        )
        self.last_tick: datetime | None = None
        """End of the previous window (in memory: no backfill across restarts)."""

    def tick(self) -> list[str]:
        """Probe the slots due in ``(last tick, now]``; return the emitted event ids."""
        now = self._clock()
        start = self.last_tick
        if start is None or now <= start:
            if start is None:
                self.last_tick = now  # startup: nothing before this instant fires
            return []
        emitted: list[str] = []
        snapshot = None
        for rule in self._probe_rules():
            slots = self._slots(rule, start, now)
            if not slots:
                continue
            actor = self._actor(rule)
            if actor is None:
                continue
            if actor.machine is not None:
                if actor.machine != self.host:
                    continue
            elif rule.placement is not None:
                if snapshot is None:
                    snapshot = self.firing._snapshot(self.store)
                if not self._placed_here(rule, snapshot, slots[0]):
                    continue
            for slot in slots:
                if self._probe(rule, actor, slot):
                    emitted.append(probe_event_id(rule.id, slot))
        self.last_tick = now  # only once every due slot is committed (or already was)
        return emitted

    # ------------------------------------------------------------------ helpers

    def _probe_rules(self) -> list[Rule]:
        rules = []
        for doc in self.store.find("rules"):
            if doc.get("deleted_at") or (doc.get("trigger") or {}).get("kind") != PROBE_KIND:
                continue
            rule = Rule.from_dict(doc, strict=False)
            if rule.enabled:
                rules.append(rule)
        return sorted(rules, key=lambda r: r.id)

    def _slots(self, rule: Rule, start: datetime, now: datetime) -> list[str]:
        try:
            cron = parse(rule.trigger.params["schedule"])
            due = cron.slots_between(start, now + _EPSILON, rule.trigger.params.get("tz"))
            return [slot_key(s) for s in due if s > start]
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("probe rule %s skipped: bad schedule: %s", rule.id, exc)
            return []

    def _actor(self, rule: Rule) -> Actor | None:
        actor_id = rule.trigger.params.get("actor")
        doc = self.store.get(ACTORS_COLLECTION, actor_id) if isinstance(actor_id, str) else None
        if doc is None or doc.get("deleted_at"):
            log.warning("probe rule %s skipped: actor %r not found", rule.id, actor_id)
            return None
        actor = Actor.from_dict(doc, strict=False)
        return actor if actor.enabled else None

    def _placed_here(self, rule: Rule, snapshot: tuple, slot: str) -> bool:
        try:
            return self.firing._mine(rule, snapshot, probe_event_id(rule.id, slot))
        except Deferred as deferred:  # ours, but drained/offline: the slot is not kept
            log.info("probe %s", deferred)
            return False

    def _probe(self, rule: Rule, actor: Actor, slot: str) -> bool:
        """Run the command for ``(rule, slot)``; True when an event was emitted."""
        params = rule.trigger.params
        event_id = probe_event_id(rule.id, slot)
        if self.store.get(EVENTS_COLLECTION, event_id) is not None:
            return False  # already handled (another process, or before a restart)
        args = params.get("args") if isinstance(params.get("args"), Mapping) else {}
        try:
            result = self._runner(actor, str(params["command"]), args, event_id)
        except Exception as exc:  # noqa: BLE001 - one bad probe must not stop the others
            log.warning("probe %s slot %s failed: %s", rule.id, slot, type(exc).__name__)
            return False
        if result.outcome != COMPLETED:
            log.info("probe %s slot %s: command %s, nothing emitted", rule.id, slot, result.outcome)
            return False
        output = result.output
        stdout = str(output.get("stdout") or "")
        exit_code = output.get("exit_code", 0)
        parsed = _parse_json_object(stdout)
        digest = _digest(parsed, stdout)
        data = dict(parsed or {})
        data.update(stdout=stdout[:MAX_STDOUT], exit_code=exit_code, rule_id=rule.id, slot=slot)
        envelope = {
            "id": event_id,
            "kind": PROBE_KIND,
            "type": PROBE_KIND,
            "source": PROBE_SOURCE,
            "time": slot,
            "data": data,
        }
        change_mode = params.get("mode") == "change"

        def insert(tx: StoreOps) -> bool:
            if tx.get(EVENTS_COLLECTION, event_id) is not None:
                return False
            if change_mode:
                previous = tx.get(PROBE_STATE, rule.id)
                if previous is not None and previous.get("digest") == digest:
                    return False
                tx.put(PROBE_STATE, {"id": rule.id, "digest": digest, "slot": slot})
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
            log.info("probe slot %s of rule %s emitted", slot, rule.id)
        return created


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _digest(parsed: Mapping[str, Any] | None, stdout: str) -> str:
    """A stable digest: canonical JSON when the output is an object, else stripped text."""
    basis = (
        json.dumps(parsed, sort_keys=True, separators=(",", ":"), default=str)
        if parsed is not None
        else stdout.strip()
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()
