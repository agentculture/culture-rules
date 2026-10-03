"""The engine node: one host's rules engine, composed from the engine's parts.

A :class:`Node` on host ``H`` does, every cycle (:meth:`Node.run_once`):

1. **heartbeat** - at :meth:`Node.start` it probes the platform
   (:func:`~culture_rules.machines.probe.probe_platform`) and publishes a heartbeat
   carrying the probed tools (and GPU load when readable); later cycles re-beat every
   :attr:`HeartbeatOptions.beat_every` seconds. Under :meth:`Node.run` a daemon thread
   beats on that cadence as well, so a long synchronous step in the drive stage never
   makes this host look offline; the thread stops when :meth:`Node.run` returns (or the
   process dies). A single :meth:`Node.run_once` (``node run --once``) beats once;
2. **ingest** - drains its own event subscription into the ``events`` collection
   (:class:`~culture_rules.events.ingest.EventIngest`; no source = no ingest);
3. **evaluate** - polls the per-host consumer for rules placed on ``H`` and the shared
   consumer for unplaced rules (:mod:`culture_rules.node.firing`), committing firing
   intents; a rule placed on ``H`` while ``H`` is drained/offline keeps its event; then
   the two chain consumers (placed on ``H`` / shared), which re-evaluate a rule waiting
   for a ``must_after`` / ``may_after`` predecessor once that predecessor's run for the
   event has finished (or it can no longer run);
4. **start** - turns pending intents into runs (run id derived from rule + event);
5. **drive** - ticks the :class:`~culture_rules.engine.runs.Executor` until idle; actors
   are reached through :class:`~culture_rules.node.actors.ActorRouter`;
6. **report** - optional: posts finished runs this node started through
   :meth:`~culture_rules.engine.reports.RunReporter.observe`.

Each stage is isolated: an exception in one is logged, recorded on the cycle report and in
:attr:`Node.errors`, and the remaining stages still run; the next cycle retries (a
:class:`~culture_rules.store.port.TransientStoreError` is just that). Only a
``BaseException`` (process death, ``KeyboardInterrupt``) ends :meth:`Node.run`; a
:meth:`Node.stop` ends it gracefully after the current cycle. Logs are emitted with
:func:`~culture_rules.ops.logs.log_context` (``host``, and ``run_id`` where one applies),
so a :class:`~culture_rules.ops.logs.JsonFormatter` writes them as JSON with that context.
Standard-library only.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from culture_rules.engine.claims import CLAIMS_COLLECTION, DEFAULT_LEASE
from culture_rules.engine.decisions import RULE_DECISIONS
from culture_rules.engine.reports import RunReporter
from culture_rules.engine.runs import RUNS_COLLECTION, Executor
from culture_rules.events.ingest import EVENTS_COLLECTION, EventIngest
from culture_rules.events.source import EventSource
from culture_rules.events.triggers import FIRES_COLLECTION
from culture_rules.machines.enrol import MACHINES_COLLECTION
from culture_rules.machines.heartbeat import (
    HEARTBEAT_COLLECTION,
    HEARTBEAT_INTERVAL_S,
    MISSED_BEATS_OFFLINE,
    HeartbeatPublisher,
)
from culture_rules.machines.probe import ProbeResult, probe_platform, read_load
from culture_rules.node.actors import ACTORS_COLLECTION, ActorRouter, AdapterFactory
from culture_rules.node.firing import RULE_FIRES, RuleFiring
from culture_rules.ops.logs import log_context
from culture_rules.store.port import Change, Document, StoragePort

__all__ = ["NODE_COLLECTIONS", "CycleReport", "HeartbeatOptions", "Node"]

log = logging.getLogger("culture_rules.node")

NODE_COLLECTIONS = (
    "rules",
    "workflows",
    ACTORS_COLLECTION,
    MACHINES_COLLECTION,
    HEARTBEAT_COLLECTION,
    EVENTS_COLLECTION,
    FIRES_COLLECTION,
    RULE_FIRES,
    RULE_DECISIONS,
    "actor_usage",
)
"""Collections a node touches (created up front on MongoDB)."""

DEFAULT_MAX_TICKS = 100


@dataclass(frozen=True, kw_only=True)
class HeartbeatOptions:
    """How a :class:`Node` probes its platform and beats (inject fakes in tests)."""

    probe: Callable[[], ProbeResult] = probe_platform
    """Probes the platform once, at :meth:`Node.start`, for tools and GPU load."""
    load_reader: Callable[[], Mapping[str, float]] = read_load
    """Reads the current load, on every beat."""
    engine_version: str | None = None
    """Published on the heartbeat (``None``: the installed version)."""
    beat_every: float = HEARTBEAT_INTERVAL_S
    """Seconds between heartbeats after the first."""


@dataclass
class CycleReport:
    """What one :meth:`Node.run_once` did."""

    host: str
    beat: bool = False
    ingested: int = 0
    duplicates: int = 0
    evaluated: list[dict[str, str]] = field(default_factory=list)
    deferred: list[dict[str, str]] = field(default_factory=list)
    started: list[str] = field(default_factory=list)
    transitions: int = 0
    reported: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _OwnRuns:
    """A store view whose ``runs`` feed only shows runs this node started (for reports)."""

    def __init__(self, store: StoragePort, identity: str) -> None:
        self._store = store
        self._identity = identity

    def changes(self, collection: str, after: str, *, timeout: float = 0.0) -> Iterator[Change]:
        for change in self._store.changes(collection, after, timeout=timeout):
            doc = change.document
            if doc is None or doc.get("started_by") == self._identity:
                yield change
            else:  # keep the token moving without reporting another node's run
                yield Change(change.token, change.collection, change.op, change.id, None)


class Node:
    """One host's engine daemon (see the module docstring)."""

    def __init__(
        self,
        store: StoragePort,
        host: str,
        *,
        actors: Mapping[str, Any] | None = None,
        adapters: Mapping[str, AdapterFactory] | None = None,
        event_source: EventSource | None = None,
        clock: Callable[[], datetime] | None = None,
        lease: timedelta = DEFAULT_LEASE,
        heartbeat_options: HeartbeatOptions | None = None,
        reporter: RunReporter | None = None,
        on_evaluated: Callable[[str, str], None] | None = None,
        max_ticks: int = DEFAULT_MAX_TICKS,
    ) -> None:
        if not isinstance(host, str) or not host:
            raise ValueError("host must be a non-empty string")
        self._store = store
        self.host = host
        self._clock = clock or (lambda: datetime.now(UTC))
        ensure = getattr(store, "ensure_collections", None)
        if callable(ensure):
            ensure(*NODE_COLLECTIONS)
        self._beat_options = heartbeat_options or HeartbeatOptions()
        self.router = ActorRouter(store, ports=actors, factories=adapters, clock=self._clock)
        self.executor = Executor(
            store,
            host,
            self.router,
            clock=self._clock,
            lease=lease,
            # a holder is offline once it missed 3 of *this cluster's* beats
            holder_offline_after=timedelta(
                seconds=MISSED_BEATS_OFFLINE * self._beat_options.beat_every
            ),
        )
        self.firing = RuleFiring(store, host, self.executor, clock=self._clock)
        self.ingest = (
            EventIngest(store, event_source, host=host, clock=self._clock)
            if event_source is not None
            else None
        )
        self.heartbeat: HeartbeatPublisher | None = None
        self._last_beat: datetime | None = None
        self._reporter = reporter
        self._report_token: str | None = None
        self._on_evaluated = on_evaluated
        self._max_ticks = max_ticks
        self._stop = threading.Event()
        self.errors: list[Exception] = []
        self.started = False

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> Document:
        """Probe, beat, and pin the trigger and chain cursors (so later changes are not
        missed)."""
        with log_context(host=self.host):
            result = self._beat_options.probe()
            gpu = result.gpu_load

            def load() -> dict[str, float]:
                reading = dict(self._beat_options.load_reader())
                if gpu is not None:
                    reading.setdefault("gpu", gpu)
                return reading

            self.heartbeat = HeartbeatPublisher(
                self._store,
                self.host,
                tools=result.tools,
                clock=self._clock,
                load_reader=load,
                engine_version=self._beat_options.engine_version,
            )
            doc = self.beat()
            pinned = CycleReport(self.host)
            for consumer in self.firing.consumers:
                self._stage(pinned, self._poll, consumer, pinned)  # retried next cycle
            if self._reporter is not None:
                self._report_token = self._store.head(RUNS_COLLECTION)
            self.started = True
            log.info("engine node %s started (tools: %s)", self.host, result.tools)
        return doc

    def beat(self) -> Document:
        """Publish a heartbeat now."""
        if self.heartbeat is None:
            raise RuntimeError("start() the node before beating")
        doc = self.heartbeat.beat()
        self._last_beat = self._clock()
        return doc

    def stop(self) -> None:
        """Ask :meth:`run` to return after the current cycle."""
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def run(self, *, idle: float = 1.0, max_cycles: int | None = None) -> int:
        """Cycle until :meth:`stop` (or ``max_cycles``), pausing ``idle`` s; return cycles.

        Heartbeats are published from a daemon thread for as long as this runs."""
        self._stop.clear()
        if not self.started:
            self._stage(CycleReport(self.host), self.start)  # retried by the first cycle
        beats = threading.Event()
        beater = threading.Thread(
            target=self._beat_loop, args=(beats,), name=f"heartbeat-{self.host}", daemon=True
        )
        beater.start()
        cycles = 0
        try:
            while not self._stop.is_set():
                self.run_once()
                cycles += 1
                if max_cycles is not None and cycles >= max_cycles:
                    break
                self._stop.wait(idle)
        finally:
            beats.set()
            beater.join(timeout=max(1.0, 2 * self._beat_options.beat_every))
        log.info("engine node %s stopped after %d cycles", self.host, cycles)
        return cycles

    def _beat_loop(self, stop: threading.Event) -> None:
        """Beat every :attr:`HeartbeatOptions.beat_every` s until ``stop`` is set."""
        with log_context(host=self.host):
            while not stop.wait(self._beat_options.beat_every):
                if self.heartbeat is None:
                    continue  # start() failed; the next cycle retries it
                try:
                    self.beat()
                except Exception as exc:  # noqa: BLE001 - keep the cadence; recorded
                    self.errors.append(exc)
                    log.warning("node %s heartbeat failed: %s", self.host, exc)

    # ------------------------------------------------------------------ one cycle

    def run_once(self) -> CycleReport:
        """One full cycle: beat (if due), ingest, evaluate, start, drive, report."""
        report = CycleReport(self.host)
        with log_context(host=self.host):
            if not self.started:
                self._stage(report, self.start)
            self._stage(report, self._beat_if_due, report)
            if self.ingest is not None:
                self._stage(report, self._ingest, report)
            for consumer in self.firing.consumers:
                self._stage(report, self._poll, consumer, report)
            self._stage(report, self._start_fired, report)
            self._stage(report, self._drive, report)
            if self._reporter is not None and self._report_token is not None:
                self._stage(report, self._report, report)
        return report

    def _stage(self, report: CycleReport, fn: Callable[..., Any], *args: Any) -> None:
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001 - a node keeps going; recorded and retried
            self.errors.append(exc)
            report.errors.append(f"{type(exc).__name__}: {exc}")
            log.warning("node %s stage %s failed: %s", self.host, fn.__name__, exc)

    def _beat_if_due(self, report: CycleReport) -> None:
        now = self._clock()
        due = self._last_beat is None or (now - self._last_beat).total_seconds() >= (
            self._beat_options.beat_every
        )
        if due:
            self.beat()
            report.beat = True

    def _ingest(self, report: CycleReport) -> None:
        for result in self.ingest.ingest():
            report.ingested += result.inserted
            report.duplicates += result.duplicates

    def _poll(self, consumer: Any, report: CycleReport) -> None:
        outcome = self.firing.poll(consumer)
        for rule_id, event_id in outcome.evaluated:
            report.evaluated.append({"rule": rule_id, "event": event_id})
            if self._on_evaluated is not None:
                self._on_evaluated(rule_id, event_id)
        report.deferred += outcome.deferred
        if outcome.error is not None:
            raise outcome.error

    def _start_fired(self, report: CycleReport) -> None:
        report.started += self.firing.start_fired()

    def _drive(self, report: CycleReport) -> None:
        report.transitions += self.executor.run_until_idle(self._max_ticks)

    def _report(self, report: CycleReport) -> None:
        reporter = self._reporter
        before = len(getattr(reporter, "_reported", ()))
        view = _OwnRuns(self._store, self.executor.identity)
        self._report_token = reporter.observe(view, self._report_token)  # type: ignore[arg-type]
        report.reported += len(getattr(reporter, "_reported", ())) - before

    # ------------------------------------------------------------------ completions

    def deliver(self, idempotency_key: str, result: Any) -> bool:
        """Record accepted work's completion and free its actor's limit slot."""
        changed = self.executor.deliver(idempotency_key, result)
        if changed and result.outcome in ("completed", "failed"):
            actor_id = self._actor_of(idempotency_key)
            if actor_id:
                self.router.release(actor_id, idempotency_key, result)
        return changed

    def _actor_of(self, key: str) -> str | None:
        claim = self._store.get(CLAIMS_COLLECTION, key)
        if not claim or claim.get("kind") != "step":
            return None
        run = self._store.get(RUNS_COLLECTION, claim["run_id"]) or {}
        definition = (run.get("workflow") or {}).get("definition") or {}
        step_id = claim["step_id"].rsplit("/", 1)[-1]
        for step in _all_steps(definition.get("steps") or ()):
            if step.get("id") == step_id:
                return (step.get("placement") or {}).get("actor")
        return None


def _all_steps(steps: Any) -> Iterator[Mapping[str, Any]]:
    for step in steps:
        yield step
        yield from _all_steps(step.get("body") or ())
