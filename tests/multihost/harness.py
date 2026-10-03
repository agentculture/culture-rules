"""A simulated three-host cluster (spark, thor, spark2) on one shared store, for t39.

Each :class:`SimHost` is one engine node: its own store handle (a MemoryStore ``peer`` or
its own MongoDB client), its own :class:`~culture_rules.engine.runs.Executor`, its own
events-cli subscription drained by :class:`~culture_rules.events.ingest.EventIngest`, its
own :class:`~culture_rules.events.triggers.EventTriggers` and its own heartbeat, all driven
by one thread. Hosts are names (strings); nothing here opens an ssh session or talks to a
real host. API instances are real ``uvicorn`` listeners on loopback ports, one per host.

Event -> rule -> run (production code since t41)
================================================
Each host is a production :class:`culture_rules.node.daemon.Node`
(:mod:`culture_rules.node.firing` holds the design below, lifted from this harness):

* every host ingests every event from its own subscription (the ``events`` collection
  dedups by envelope id);
* **placed rules** are evaluated through a per-host trigger consumer ``triggers@<host>``
  whose handler only looks at rules whose placement
  (:func:`~culture_rules.engine.placement.resolve_rule_placement` over enrolled machines,
  heartbeats and drain flags) resolves to *this* host - so a rule placed on B is evaluated
  on B and nowhere else, and a stopped B resumes its own backlog when it restarts;
* **unplaced rules** are evaluated through one shared consumer ``triggers``: whichever host
  fires an event first commits the fire marker, so each event is evaluated once;
* a firing decision is committed in the trigger transaction as a ``rule_fires`` intent
  (id = :func:`~culture_rules.engine.claims.firing_key`), and any eligible host turns a
  pending intent into a run whose id is derived from ``(rule, event)``
  (:func:`run_id_for`) - a second start is a duplicate key, so a rule fires exactly once
  per event even when two hosts race or one dies between the fire and the start.

Side effects go to one shared :class:`Ledger` - the "outside world" every host's actor
port reaches - which, like any ActorPort adapter (obligation o6), is idempotent on the
idempotency key and records every invocation and every side effect it performed.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.engine.claims import idempotency_key
from culture_rules.engine.runs import RUN_DONE, RUNS_COLLECTION, step_state
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.events.triggers import FIRES_COLLECTION
from culture_rules.machines.enrol import MACHINES_COLLECTION, enrol
from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION
from culture_rules.machines.probe import ProbeResult
from culture_rules.model.action import Action
from culture_rules.model.machine import Machine
from culture_rules.model.placement import Placement
from culture_rules.model.rule import Rule, Trigger, WorkflowRef
from culture_rules.model.workflow import Output, Workflow
from culture_rules.node.daemon import HeartbeatOptions, Node
from culture_rules.node.firing import RULE_FIRES, run_id_for  # noqa: F401 - re-exported
from culture_rules.store.port import StoragePort
from tests.engine.run_helpers import edge, port, step, workflow
from tests.events.fakes import FakeEventSource

HOSTS = ["spark", "thor", "spark2"]
LEASE = timedelta(seconds=2)
"""Short claim lease so survivors reclaim a dead host's step quickly."""
LOOP_PAUSE_S = 0.02
BEAT_EVERY_S = 1.0
EVENT_TYPE = "task.requested"

_COLLECTIONS = (
    "rules",
    "workflows",
    "actors",
    MACHINES_COLLECTION,
    HEARTBEAT_COLLECTION,
    EVENTS_COLLECTION,
    FIRES_COLLECTION,
    RULE_FIRES,
)


class HostKilled(BaseException):
    """The host process dies (not an ordinary Exception: nothing in the engine catches it)."""


# --------------------------------------------------------------------------- builders


def event_rule(id: str, workflow_id: str | None = None, placement: Placement | None = None) -> Rule:
    """A rule fired by ``task.requested`` events; its action records the event id."""
    return Rule(
        id=id,
        name=id,
        trigger=Trigger(kind="event", params={"type": EVENT_TYPE}),
        workflow=WorkflowRef(id=workflow_id) if workflow_id else None,
        action=Action(kind="noop", params={"event": "trigger.id"}),
        placement=placement,
    )


def three_host_workflow() -> Workflow:
    """s1 on spark -> s2 on thor -> s3 on spark2, typed integer -> number -> string."""
    return workflow(
        (
            step("s1", placement=Placement(machine="spark"), outputs=(port("n", "integer"),)),
            step(
                "s2",
                "ai",
                placement=Placement(machine="thor"),
                inputs=(port("n", "number"),),
                outputs=(port("x", "number"),),
            ),
            step(
                "s3",
                placement=Placement(machine="spark2"),
                inputs=(port("x", "number"),),
                outputs=(port("msg", "string"),),
            ),
        ),
        (edge("s1", "n", "s2", "n"), edge("s2", "x", "s3", "x")),
        outputs=(Output(name="msg", type="string", source="steps.s3.outputs.msg"),),
    )


def _outputs(step_id: str, inp: Mapping[str, Any]) -> dict[str, Any]:
    if step_id == "s1":
        return {"n": 2}
    if step_id == "s2":
        return {"x": inp["n"] * 1.5}
    if step_id == "s3":
        return {"msg": f"got {inp['x']}"}
    return {}


# --------------------------------------------------------------------------- outside world


@dataclass(frozen=True)
class Effect:
    at: float
    host: str
    run_id: str
    step: str
    key: str
    input: dict[str, Any]


class Ledger:
    """The shared outside world: an idempotent ActorPort target recording every effect."""

    supports_idempotency_key = True

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._done: dict[str, InvocationResult] = {}
        self.log: list[Effect] = []
        self.invocations: list[tuple[float, str, str, str, str]] = []

    @staticmethod
    def key(run_id: str, step_id: str) -> str:
        return idempotency_key(run_id, step_id)

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        with self._lock:
            now = time.time()
            self.invocations.append(
                (now, context.host, context.run_id, context.step_id, idempotency_key)
            )
            done = self._done.get(idempotency_key)
            if done is not None:  # a retry of work already done: deduplicate
                return done
            result = InvocationResult.completed(_outputs(context.step_id, input))
            self.log.append(
                Effect(
                    now, context.host, context.run_id, context.step_id, idempotency_key, dict(input)
                )
            )
            self._done[idempotency_key] = result
            return result

    def effects(self, step: str | None = None) -> dict[str, int]:
        """Idempotency key -> number of side effects performed for it."""
        counts: dict[str, int] = defaultdict(int)
        with self._lock:
            for e in self.log:
                if step is None or e.step == step:
                    counts[e.key] += 1
        return dict(counts)

    def invocation_counts(self, step: str | None = None) -> dict[str, int]:
        """Idempotency key -> number of times the engine invoked it."""
        counts: dict[str, int] = defaultdict(int)
        with self._lock:
            for _, _, _, step_id, key in self.invocations:
                if step is None or step_id == step:
                    counts[key] += 1
        return dict(counts)

    def effects_by_host(self, step: str | None = None) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        with self._lock:
            for e in self.log:
                if step is None or e.step == step:
                    counts[e.host] += 1
        return dict(counts)

    def hosts_by_step(self, run_id: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        with self._lock:
            for e in self.log:
                if e.run_id == run_id:
                    out[e.step].append(e.host)
        return dict(out)

    def hosts_after(self, moment: float | None, *, step: str | None = None) -> set[str]:
        with self._lock:
            return {
                h
                for at, h, _, s, _ in self.invocations
                if moment is not None and at >= moment and (step is None or s == step)
            }


class _HostPort:
    """One host's view of the outside world; can kill its host mid-action."""

    supports_idempotency_key = True

    def __init__(self, host: SimHost, ledger: Ledger) -> None:
        self._host = host
        self._ledger = ledger

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        result = self._ledger.invoke(input, idempotency_key, deadline, context=context)
        if self._host.crash_armed and context.kind == "action":
            self._host.crashed_key = idempotency_key
            raise HostKilled(f"{self._host.name} died after performing {context.step_id}")
        return result


# --------------------------------------------------------------------------- engine node


class Broker:
    """events-cli stand-in: every published envelope reaches every host's subscription."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: dict[str, FakeEventSource] = {}

    def subscription(self, host: str) -> FakeEventSource:
        with self._lock:
            return self._subs.setdefault(host, FakeEventSource(name=f"culture-rules@{host}"))

    def publish(self, envelope: Mapping[str, Any]) -> None:
        with self._lock:
            for source in self._subs.values():
                source.publish(dict(envelope))


class SimHost:
    """One engine node in its own thread: a production :class:`~culture_rules.node.daemon.Node`
    (heartbeat, ingest, placed/unplaced triggers, intents, executor) driven by
    :meth:`Node.run`, plus the harness's crash switch."""

    def __init__(self, cluster: Cluster, name: str, store: StoragePort) -> None:
        self.cluster = cluster
        self.name = name
        self.store = store
        self.node = Node(
            store,
            name,
            actors={"*": _HostPort(self, cluster.ledger)},
            event_source=cluster.broker.subscription(name),
            lease=LEASE,
            heartbeat_options=HeartbeatOptions(
                probe=lambda: ProbeResult(tools={}),
                load_reader=lambda: {"cpu": 0.0, "mem": 0.0},
                engine_version="test",
                beat_every=BEAT_EVERY_S,
            ),
            on_evaluated=lambda rule_id, event_id: cluster._evaluated(name, rule_id, event_id),
        )
        self._hold_drive(cluster)
        self.crash_armed = False
        self.crashed_key: str | None = None
        self.killed = False
        self.killed_at: float | None = None
        self._thread = threading.Thread(target=self._loop, name=f"host-{name}", daemon=True)

    def _hold_drive(self, cluster: Cluster) -> None:
        """Let :meth:`Cluster.kill` hold this host's executor (it still ingests, evaluates
        and starts runs) so the armed victim, not the first claimant, takes the next action.

        The hold is checked per run advance, not per drive: a drive already in progress
        when the hold begins (one ``run_until_idle`` can tick through every active run)
        stops after the run it is on instead of taking the whole backlog."""
        executor = self.node.executor
        advance = executor._advance

        def held_or_advance(run_id: str) -> int:
            return 0 if cluster.holding(self.name) else advance(run_id)

        executor._advance = held_or_advance  # type: ignore[method-assign]

    @property
    def errors(self) -> list[Exception]:
        return self.node.errors

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self.node.start()  # beats and pins both trigger cursors before any event arrives
        self._thread.start()

    def stop(self) -> None:
        self.node.stop()
        self._thread.join(timeout=30)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def _loop(self) -> None:
        try:
            self.node.run(idle=LOOP_PAUSE_S)
        except HostKilled:
            self.killed = True
            self.killed_at = time.time()


# --------------------------------------------------------------------------- API instances


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def issue_token(store: StoragePort, roles: tuple[str, ...] = ("editor",)) -> str:
    """A real service token (t24): the harness authenticates as a production client does."""
    from culture_rules.auth.tokens import ServiceTokens
    from culture_rules.engine.audit import AuditLog

    issued = ServiceTokens(store, AuditLog()).issue("chaos-test", name="chaos-test", roles=roles)
    return issued.token


class ApiServer:
    """One host's HTTP API: a real uvicorn listener on a loopback port, in a thread."""

    def __init__(self, store: StoragePort, host: str) -> None:
        uvicorn = pytest.importorskip("uvicorn")
        pytest.importorskip("fastapi")
        from culture_rules.server.app import create_app

        self.host = host
        self.headers = {"Authorization": f"Bearer {issue_token(store)}"}
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(
            create_app(store, host=host),
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            lifespan="off",
            timeout_graceful_shutdown=1,
        )
        self.server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self.server.run, name=f"api-{host}", daemon=True)

    def start(self) -> ApiServer:
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError(f"API on {self.host} did not start")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self._thread.join(timeout=15)

    def sse(
        self, collections: Iterable[str], *, run_id: str, until: Callable[[dict], bool]
    ) -> SseReader:
        return SseReader(
            self.url, list(collections), run_id=run_id, until=until, headers=self.headers
        )


class SseReader:
    """Reads ``/events/stream`` in a thread, stamping every frame with its arrival time."""

    def __init__(
        self,
        url: str,
        collections: list[str],
        *,
        run_id: str,
        until: Callable[[dict], bool],
        headers: dict[str, str] | None = None,
    ) -> None:
        self._url = url
        self._headers = headers or {}
        self._collections = collections
        self._run_id = run_id
        self._until = until
        self.frames: list[tuple[datetime, dict[str, Any]]] = []
        self.error: BaseException | None = None
        self._ready = threading.Event()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._read, name="sse-reader", daemon=True)

    def __enter__(self) -> SseReader:
        self._thread.start()
        if not self._ready.wait(15):
            raise AssertionError(f"SSE stream never opened: {self.error!r}")
        return self

    def __exit__(self, *exc: Any) -> None:
        self._done.set()
        self._thread.join(timeout=5)

    def _read(self) -> None:
        import httpx

        params = {"collections": ",".join(self._collections), "max_seconds": "60"}
        try:
            timeout = httpx.Timeout(5.0, read=None)
            with httpx.Client(timeout=timeout, headers=self._headers) as client:
                with client.stream("GET", f"{self._url}/events/stream", params=params) as resp:
                    self._consume(resp.iter_lines())
        except BaseException as exc:  # noqa: BLE001 - surfaced by wait()
            self.error = exc
        finally:
            self._ready.set()

    def _consume(self, lines: Iterable[str]) -> None:
        data: list[str] = []
        for line in lines:
            if line.startswith("retry:"):
                self._ready.set()  # the stream's cursors are pinned: later writes will show
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif line == "" and data:
                payload = json.loads("\n".join(data))
                data = []
                self.frames.append((datetime.now(UTC), payload))
                doc = payload.get("document") or {}
                if payload.get("id") == self._run_id and self._until(doc):
                    self._done.set()
            if self._done.is_set():
                return

    def wait(self, timeout: float) -> None:
        if not self._done.wait(timeout) or self.error is not None:
            raise AssertionError(f"SSE never showed the finished run (error={self.error!r})")

    def first_seen(self, run_id: str, key: str, status: str) -> datetime | None:
        for at, payload in self.frames:
            doc = payload.get("document") or {}
            if payload.get("id") == run_id and (step_state(doc, key) or {}).get("status") == status:
                return at
        return None


class FailoverClient:
    """Stands in for the edge: round-robins over origins, moving to the next origin only
    when one cannot be reached (connection-level failure). HTTP errors are never retried."""

    def __init__(self, urls: list[str], headers: dict[str, str] | None = None) -> None:
        import httpx

        self._httpx = httpx
        self.headers = headers or {}
        self.urls = urls
        self._next = 0
        self.failovers = 0

    def request(self, client: Any, method: str, path: str, **kw: Any) -> Any:
        n = len(self.urls)
        start, self._next = self._next, self._next + 1
        for j in range(n):
            try:
                return client.request(method, self.urls[(start + j) % n] + path, **kw)
            except self._httpx.TransportError:
                self.failovers += 1
        return None

    def mixed_load(self, count: int) -> list[bool]:
        """``count`` requests mixing reads and definition writes; True per success."""
        outcomes: list[bool] = []
        with self._httpx.Client(timeout=10.0, headers=self.headers) as client:
            for i in range(count):
                kind = i % 5
                if kind == 0:
                    resp, want = self.request(client, "GET", "/health"), 200
                elif kind == 1:
                    resp, want = self.request(client, "GET", "/rules"), 200
                elif kind == 2:
                    resp, want = self.request(client, "GET", "/runs?limit=5"), 200
                elif kind == 3:
                    body = Rule(
                        id=f"load-{i}",
                        name=f"load-{i}",
                        trigger=Trigger(kind="manual"),
                        action=Action(kind="noop"),
                    ).to_dict()
                    resp, want = self.request(client, "POST", "/rules", json=body), 201
                else:  # read the previous write back through (probably) another instance
                    resp, want = self.request(client, "GET", f"/rules/load-{i - 1}"), 200
                outcomes.append(resp is not None and resp.status_code == want)
        return outcomes


# --------------------------------------------------------------------------- cluster


class Cluster:
    """The three simulated hosts plus their API instances, on one shared store."""

    def __init__(self, peer: Callable[[], StoragePort], *, backend: str) -> None:
        self._peer = peer
        self.backend = backend
        self.store = peer()  # the operator's / test's own handle
        ensure = getattr(self.store, "ensure_collections", None)
        if callable(ensure):
            ensure(*_COLLECTIONS)
        self.ledger = Ledger()
        self.broker = Broker()
        self._hosts: dict[str, SimHost] = {}
        self._every_host: list[SimHost] = []
        self._apis: dict[str, ApiServer] = {}
        self._held: frozenset[str] = frozenset()
        self._lock = threading.Lock()
        self._evals: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for name in HOSTS:
            enrol(self.store, Machine(name=name, roles=("engine_node",)), apply=True)

    # ------------------------------------------------------------------ setup

    def define(self, *models: Rule | Workflow) -> None:
        for model in models:
            collection = "rules" if isinstance(model, Rule) else "workflows"
            self.store.put(collection, model.to_dict())

    def start(self, *names: str) -> None:
        for name in names:
            current = self._hosts.get(name)
            if current is not None and current.alive:
                raise RuntimeError(f"{name} is already running")
            host = SimHost(self, name, self._peer())
            self._hosts[name] = host
            self._every_host.append(host)
            host.start()

    def serve(self, name: str) -> ApiServer:
        api = ApiServer(self._peer(), name).start()
        self._apis[name] = api
        return api

    def host(self, name: str) -> SimHost:
        return self._hosts[name]

    # ------------------------------------------------------------------ chaos

    def stop(self, name: str) -> None:
        """Stop a host cleanly: its engine loop and its API listener."""
        self._hosts[name].stop()
        api = self._apis.pop(name, None)
        if api is not None:
            api.stop()

    def kill(self, name: str, timeout: float = 30.0) -> None:
        """Kill a host abruptly: it dies right after its next action side effect, before
        recording it (step left ``dispatching``, claim held); its API listener goes away.

        Unplaced work goes to whichever host claims it first, and the host loops can settle
        into a phase where the victim loses every claim race for the whole run (seen on
        MongoDB: thor performing 0 of 100 actions), so it would never reach an action and
        never die. Until it does, the other hosts' executors are held (they keep ingesting,
        evaluating and starting runs); they resume - and must reclaim the orphaned step
        after the lease - as soon as the victim is dead."""
        host = self._hosts[name]
        api = self._apis.pop(name, None)
        if api is not None:
            api.stop()
        self._held = frozenset(h for h in self._hosts if h != name)
        host.crash_armed = True
        try:
            self.wait_until(lambda: host.killed, timeout=timeout, what=f"{name} to die mid-action")
        finally:
            self._held = frozenset()

    def holding(self, name: str) -> bool:
        """Whether ``name``'s executor is held while another host's kill is pending."""
        return name in self._held

    # ------------------------------------------------------------------ events

    def publish(self, envelope: Mapping[str, Any]) -> None:
        self.broker.publish(envelope)

    def _evaluated(self, host: str, rule_id: str, event_id: str) -> None:
        with self._lock:
            self._evals[rule_id][host].append(event_id)

    def evaluations(self, rule_id: str) -> dict[str, list[str]]:
        """Host -> event ids for which that host evaluated ``rule_id`` (committed only)."""
        with self._lock:
            return {h: list(ev) for h, ev in self._evals.get(rule_id, {}).items() if ev}

    def fires(self, rule_id: str) -> list[str]:
        """Event ids ``rule_id`` fired on (one ``rule_fires`` intent each)."""
        return [d["event_id"] for d in self.store.find(RULE_FIRES, {"rule_id": rule_id})]

    # ------------------------------------------------------------------ waiting

    def errors(self) -> list[Exception]:
        return [e for h in self._every_host for e in h.errors]

    def run_for(self, rule_id: str, event_id: str) -> dict[str, Any] | None:
        return self.store.get(RUNS_COLLECTION, run_id_for(rule_id, event_id))

    def wait_until(self, predicate: Callable[[], bool], timeout: float, what: str = "") -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"timed out after {timeout}s waiting for {what or predicate}; "
                    f"host errors: {self.errors()[:5]!r}"
                )
            time.sleep(0.05)

    def wait_run(self, rule_id: str, event_id: str, timeout: float) -> dict[str, Any]:
        found: dict[str, Any] = {}

        def finished() -> bool:
            doc = self.run_for(rule_id, event_id)
            if doc is not None and doc.get("status") in RUN_DONE:
                found["doc"] = doc
                return True
            return False

        self.wait_until(finished, timeout, f"run of {rule_id} on {event_id} to finish")
        return found["doc"]

    def settle(self, seconds: float) -> None:
        time.sleep(seconds)

    def close(self) -> None:
        for api in list(self._apis.values()):
            api.stop()
        self._apis.clear()
        for host in self._every_host:
            host.stop()
