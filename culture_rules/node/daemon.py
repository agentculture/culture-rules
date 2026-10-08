"""The engine node: one host's rules engine, composed from the engine's parts.

A :class:`Node` on host ``H`` does, every cycle (:meth:`Node.run_once`):

1. **heartbeat** - at :meth:`Node.start` it probes the platform
   (:func:`~culture_rules.machines.probe.probe_platform`) and publishes a heartbeat
   carrying the probed tools (and GPU load when readable) and its capabilities
   (:attr:`HeartbeatOptions.capabilities`; ``variables`` by default, see
   :mod:`culture_rules.engine.variables`); later cycles re-beat every
   :attr:`HeartbeatOptions.beat_every` seconds. Under :meth:`Node.run` a daemon thread
   beats on that cadence as well, so a long synchronous step in the drive stage never
   makes this host look offline; the thread stops when :meth:`Node.run` returns (or the
   process dies). A single :meth:`Node.run_once` (``node run --once``) beats once;
2. **ingest** - drains its own event subscription into the ``events`` collection
   (:class:`~culture_rules.events.ingest.EventIngest`; no source = no ingest);
3. **schedule** - synthesizes one ``kind=schedule`` event per cron slot that came due
   since the previous tick, for the schedule rules placed on ``H`` and the unplaced ones
   (:mod:`culture_rules.node.schedule`; exactly once per rule/slot, no backfill of slots
   missed while the node was down); they are evaluated like any other event;
   then **probe** - runs each due probe rule's allow-listed command on the actor's own
   machine and emits a ``kind=probe`` event on change or success
   (:mod:`culture_rules.node.probe_trigger`; same slot marker and window as schedule);
   then **discord gateway** - reconciles the Discord Gateway listeners
   (:class:`~culture_rules.apps.discord_gateway.GatewaySupervisor`): for each enabled
   Discord app actor declaring ``discord.message.created`` it acquires/renews the mesh-wide
   lease ``discord-gateway:<actor id>`` and keeps a listener thread connected while this
   node holds it (one connection mesh-wide); messages land through the webhook sink. The
   listeners stop when :meth:`Node.run` returns (:meth:`Node.close`). Off with
   ``NodeOptions(listen_gateways=False)`` (``node run --once`` never opens a long-lived
   connection);
4. **evaluate** - polls the per-host consumer for rules placed on ``H`` and the shared
   consumer for unplaced rules (:mod:`culture_rules.node.firing`), committing firing
   intents; a rule placed on ``H`` while ``H`` is drained/offline keeps its event; then
   the two chain consumers (placed on ``H`` / shared), which re-evaluate a rule waiting
   for a ``must_after`` / ``may_after`` predecessor once that predecessor's run for the
   event has finished (or it can no longer run);
5. **start** - turns pending intents into runs (run id derived from rule + event);
   then **redeliver** - resumes runs for human asks that were answered but whose
   delivery was lost (a crash between recording the answer and delivering it;
   :func:`~culture_rules.actors.human.redeliver`), and delivers bridge agent results the
   API recorded from a bridge's callback (:func:`~culture_rules.actors.agent.redeliver_bridge`;
   a result for an attempt the step has moved past is discarded). The run's
   compare-and-set keeps it exactly once when several nodes redeliver the same answer,
   and the actor's limit slot is freed (:mod:`culture_rules.node.completions`). Mesh
   replies are not polled: ``MeshAgentActor`` is not among the production adapters;
6. **drive** - ticks the :class:`~culture_rules.engine.runs.Executor` until idle; actors
   are reached through :class:`~culture_rules.node.actors.ActorRouter`; then **cancel** -
   asks each bridge this node can reach (an actor on this machine, or on none) to cancel
   the jobs whose step attempt is over (:func:`~culture_rules.actors.agent.cancel_orphans`,
   d21 phase 2), so an orphaned agent session never holds a bridge's seat;
7. **status** - the PR fixer's live status comments (d26,
   :mod:`culture_rules.node.status_board`): the single writer of the status comments of
   the App actors placed on this machine reconciles each to its desired state, bounded per
   cycle (requests and seconds), after the drive stage;
8. **report** - optional: posts finished runs this node started through
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

from culture_rules.actors import agent, human
from culture_rules.actors.review import CURRENT_COLLECTION, REVIEWS_COLLECTION
from culture_rules.apps.discord_gateway import (
    GATEWAY_STATE_COLLECTION,
    Gateway,
    GatewayOptions,
    GatewaySupervisor,
)
from culture_rules.engine.claims import DEFAULT_LEASE, RULE_ATTEMPT_BUDGETS
from culture_rules.engine.decisions import RULE_DECISIONS
from culture_rules.engine.named_lease import LEASES_COLLECTION
from culture_rules.engine.reports import RunReporter
from culture_rules.engine.run_completions import RUN_COMPLETIONS, RUN_EVENT_CONSUMPTION
from culture_rules.engine.runs import RUNS_COLLECTION, Executor
from culture_rules.engine.variables import NODE_CAPABILITIES, VARIABLES_CAPABILITY
from culture_rules.events.hook_sink import HOOK_STATS_COLLECTION
from culture_rules.events.ingest import (
    EVENTS_COLLECTION,
    QUARANTINE_COLLECTION,
    EventIngest,
    ensure_quarantine_ttl,
)
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
from culture_rules.node import completions
from culture_rules.node.actions.github import ONCE_COLLECTION
from culture_rules.node.actors import ACTORS_COLLECTION, ActorRouter, AdapterFactory
from culture_rules.node.chain import CHAIN_NEEDS_REVIEW
from culture_rules.node.checks_settle import (
    LATE_COLLECTION,
    RECOVERY_COLLECTION,
    SETTLE_COLLECTION,
    UNRESOLVED_RETRY_S,
    AppSuiteLister,
    ChecksSettler,
)
from culture_rules.node.firing import RULE_FIRES, RuleFiring
from culture_rules.node.fixer_status import STATUS_COLLECTION
from culture_rules.node.probe_trigger import PROBE_STATE, CommandRunner, ProbeTrigger
from culture_rules.node.schedule import Scheduler
from culture_rules.node.status_board import ensure_status_indexes
from culture_rules.ops.logs import log_context
from culture_rules.store.port import Change, Document, StoragePort

__all__ = ["NODE_COLLECTIONS", "CycleReport", "HeartbeatOptions", "Node", "NodeOptions"]

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
    human.ASKS_COLLECTION,
    agent.BRIDGE_INVOCATIONS,
    REVIEWS_COLLECTION,
    CURRENT_COLLECTION,
    PROBE_STATE,
    LEASES_COLLECTION,
    HOOK_STATS_COLLECTION,
    GATEWAY_STATE_COLLECTION,
    SETTLE_COLLECTION,
    RECOVERY_COLLECTION,
    LATE_COLLECTION,
    ONCE_COLLECTION,
    STATUS_COLLECTION,
    RULE_ATTEMPT_BUDGETS,
    RUN_COMPLETIONS,
    RUN_EVENT_CONSUMPTION,
    QUARANTINE_COLLECTION,
    CHAIN_NEEDS_REVIEW,
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
    capabilities: tuple[str, ...] = NODE_CAPABILITIES
    """Advertised on the heartbeat. Without ``variables`` this node refuses to evaluate a
    rule that references a shared variable (records ``variables_unsupported``)."""
    beat_every: float = HEARTBEAT_INTERVAL_S
    """Seconds between heartbeats after the first."""


@dataclass(frozen=True, kw_only=True)
class NodeOptions:
    """How a :class:`Node` runs its loop (the defaults suit a long-running daemon)."""

    lease: timedelta = DEFAULT_LEASE
    """The run/step claim lease the executor takes."""
    max_ticks: int = DEFAULT_MAX_TICKS
    """Executor ticks per cycle at most."""
    probe_runner: CommandRunner | None = None
    """Runs probe commands (``None``: the default runner; inject a fake in tests)."""
    listen_gateways: bool = True
    """Whether the discord gateway stage opens listeners (``node run --once`` does not)."""


@dataclass
class CycleReport:
    """What one :meth:`Node.run_once` did."""

    host: str
    beat: bool = False
    ingested: int = 0
    duplicates: int = 0
    scheduled: list[str] = field(default_factory=list)
    probed: list[str] = field(default_factory=list)
    listening: list[str] = field(default_factory=list)
    evaluated: list[dict[str, str]] = field(default_factory=list)
    deferred: list[dict[str, str]] = field(default_factory=list)
    started: list[str] = field(default_factory=list)
    redelivered: int = 0
    transitions: int = 0
    reported: int = 0
    status_edits: int = 0
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
        heartbeat_options: HeartbeatOptions | None = None,
        reporter: RunReporter | None = None,
        on_evaluated: Callable[[str, str], None] | None = None,
        discord_gateway: Gateway | None = None,
        resolve_secret: Callable[[str], str] | None = None,
        gateway_options: GatewayOptions | None = None,
        options: NodeOptions | None = None,
    ) -> None:
        if not isinstance(host, str) or not host:
            raise ValueError("host must be a non-empty string")
        self._store = store
        self.host = host
        self._clock = clock or (lambda: datetime.now(UTC))
        ensure = getattr(store, "ensure_collections", None)
        if callable(ensure):
            ensure(*NODE_COLLECTIONS)
        ensure_quarantine_ttl(store)  # every node can quarantine (outbox, ingest)
        ensure_status_indexes(store)  # d26: the status board's queries
        options = options or NodeOptions()
        self._beat_options = heartbeat_options or HeartbeatOptions()
        self.router = ActorRouter(store, ports=actors, factories=adapters, clock=self._clock)
        comment_port = (actors or {}).get("action:github.comment")
        self._status_tick = getattr(comment_port, "status_tick", None)
        self.executor = Executor(
            store,
            host,
            self.router,
            clock=self._clock,
            lease=options.lease,
            # a holder is offline once it missed 3 of *this cluster's* beats
            holder_offline_after=timedelta(
                seconds=MISSED_BEATS_OFFLINE * self._beat_options.beat_every
            ),
        )
        self.firing = RuleFiring(
            store,
            host,
            self.executor,
            clock=self._clock,
            variables=VARIABLES_CAPABILITY in self._beat_options.capabilities,
        )
        self.scheduler = Scheduler(store, host, self.firing, clock=self._clock)
        self.prober = ProbeTrigger(
            store, host, self.firing, clock=self._clock, runner=options.probe_runner
        )
        self.ingest = (
            EventIngest(store, event_source, host=host, clock=self._clock)
            if event_source is not None
            else None
        )
        self.gateways = GatewaySupervisor(
            store,
            self.executor.identity,
            gateway=discord_gateway,
            resolve_secret=resolve_secret,
            clock=self._clock,
            options=gateway_options,
            host=host,
        )
        self._listen_gateways = options.listen_gateways
        # placed: this node settles only the SHAs whose App actor it can serve (wave-3 P1)
        lister = AppSuiteLister(
            store, secrets=resolve_secret, host=host, unresolved_retry_s=UNRESOLVED_RETRY_S
        )
        self.settler = ChecksSettler(
            store,
            lister.list_suites,
            pull=lister.get_pull,
            serves=lister.serves,
            clock=self._clock,
        )
        self.heartbeat: HeartbeatPublisher | None = None
        self._last_beat: datetime | None = None
        self._reporter = reporter
        self._report_token: str | None = None
        self._on_evaluated = on_evaluated
        self._max_ticks = options.max_ticks
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
                capabilities=tuple(self._beat_options.capabilities),
            )
            doc = self.beat()
            pinned = CycleReport(self.host)
            for consumer in self.firing.start_consumers:  # cursors before the first drain
                self._stage(pinned, self._poll, consumer, pinned)  # retried next cycle
            self._stage(pinned, self._schedule, pinned)  # opens the window: no backfill
            self._stage(pinned, self._probe, pinned)
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

    def close(self) -> None:
        """Stop the gateway listeners and release their leases (another node takes over)."""
        with log_context(host=self.host):
            self.gateways.shutdown()

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
            self._stage(CycleReport(self.host), self.close)
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
        """One full cycle: beat (if due), ingest, schedule, evaluate, start, redeliver, drive,
        report."""
        report = CycleReport(self.host)
        with log_context(host=self.host):
            if not self.started:
                self._stage(report, self.start)
            self._stage(report, self._beat_if_due, report)
            if self.ingest is not None:
                self._stage(report, self._ingest, report)
            self._stage(report, self._schedule, report)
            self._stage(report, self._probe, report)
            self._stage(report, self._settle, report)
            self._stage(report, self._expire_holds, report)
            if self._listen_gateways:
                self._stage(report, self._discord_gateway, report)
            for consumer in self.firing.consumers:
                self._stage(report, self._poll, consumer, report)
            self._stage(report, self._start_fired, report)
            self._stage(report, self._redeliver, report)
            self._stage(report, self._drive, report)
            self._stage(report, self._cancel_orphans, report)
            if self._status_tick is not None:
                self._stage(report, self._fixer_status, report)
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

    def _schedule(self, report: CycleReport) -> None:
        report.scheduled += self.scheduler.tick()

    def _probe(self, report: CycleReport) -> None:
        report.probed += self.prober.tick()

    def _settle(self, report: CycleReport) -> None:
        self.settler.tick()

    def _expire_holds(self, report: CycleReport) -> None:
        """Release chain holds past their TTL (d21): their pending events then fire through
        the chain consumers (:func:`~culture_rules.engine.chain_hold.expire_holds`)."""
        del report
        from culture_rules.engine.chain_hold import expire_holds  # noqa: PLC0415

        for doc_id in expire_holds(self._store, self._clock()):
            log.warning("chain hold on %s expired before its continuation: released", doc_id)

    def _discord_gateway(self, report: CycleReport) -> None:
        report.listening += self.gateways.tick()

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

    def _redeliver(self, report: CycleReport) -> None:
        report.redelivered += human.redeliver(self._store, self.executor)
        report.redelivered += agent.redeliver_bridge(self._store, self.executor)

    def _drive(self, report: CycleReport) -> None:
        report.transitions += self.executor.run_until_idle(self._max_ticks)

    def _cancel_orphans(self, report: CycleReport) -> None:
        """Cancel bridge jobs whose step attempt is over (d21 phase 2), for the actors
        this node can reach (:func:`~culture_rules.actors.agent.cancel_orphans`)."""
        del report
        agent.cancel_orphans(self._store, self._bridge_adapter, clock=self._clock)

    def _fixer_status(self, report: CycleReport) -> None:
        """Post and edit the PR fixer's status comments (d26) for the App actors here."""
        report.status_edits += self._status_tick(self.host)

    def _bridge_adapter(self, actor_id: Any) -> Any:
        """The adapter of ``actor_id`` when this node may call its bridge: the actor lives
        on this machine (its bridge token is in this node's secrets) or on none."""
        doc = self._store.get(ACTORS_COLLECTION, actor_id) if isinstance(actor_id, str) else None
        if not doc or doc.get("deleted_at"):
            return None
        machine = doc.get("machine")
        if machine and machine != self.host:
            return None
        limited = self.router.limited(actor_id)
        return getattr(limited, "inner", None)

    def _report(self, report: CycleReport) -> None:
        reporter = self._reporter
        before = len(getattr(reporter, "_reported", ()))
        view = _OwnRuns(self._store, self.executor.identity)
        self._report_token = reporter.observe(view, self._report_token)  # type: ignore[arg-type]
        report.reported += len(getattr(reporter, "_reported", ())) - before

    # ------------------------------------------------------------------ completions

    def deliver(self, idempotency_key: str, result: Any) -> bool:
        """Record accepted work's completion and free its actor's limit slot."""
        return completions.deliver(
            self._store, self.executor, idempotency_key, result, clock=self._clock
        )
