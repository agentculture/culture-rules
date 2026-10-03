"""Criteria 1 and 5 of t41: the engine node daemon on one host, with MemoryStore + fakes.

A :class:`~culture_rules.node.daemon.Node` composes heartbeat (with the platform probe),
event ingest, placed/unplaced triggers, firing intents, the Executor, actor adapters and
the run reporter. Several nodes on ``MemoryStore.peer`` handles stand in for several hosts.
"""

from __future__ import annotations

import io
import json
import logging
import threading

import pytest

from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.claims import idempotency_key
from culture_rules.engine.reports import RunReporter
from culture_rules.engine.runs import ACTION_STEP, RUNS_COLLECTION, Containment, step_state
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION
from culture_rules.machines.probe import ProbeResult
from culture_rules.model.action import Action
from culture_rules.model.actor import Actor
from culture_rules.model.placement import Placement
from culture_rules.model.rule import Rule, Trigger, WorkflowRef
from culture_rules.node.daemon import CycleReport, Node
from culture_rules.node.firing import RULE_FIRES, run_id_for
from culture_rules.ops.logs import JsonFormatter
from culture_rules.store.memory import MemoryStore
from culture_rules.store.port import TransientStoreError
from tests.engine.run_helpers import Clock, FakeActor, enrol_online, machine, port, step, workflow
from tests.events.fakes import FakeEventSource, envelope

EVENT_TYPE = "task.requested"


def event_rule(id: str, workflow_id: str | None = None, placement: Placement | None = None):
    return Rule(
        id=id,
        name=id,
        trigger=Trigger(kind="event", params={"type": EVENT_TYPE}),
        workflow=WorkflowRef(id=workflow_id) if workflow_id else None,
        action=Action(kind="noop", params={"event": "trigger.id"}),
        placement=placement,
    )


def one_step_workflow(placement: Placement | None = None) -> object:
    return workflow((step("s1", outputs=(port("n", "integer"),), placement=placement),))


class Cluster:
    """Hosts sharing one MemoryStore, each with its own subscription and node."""

    def __init__(self, *hosts: str) -> None:
        self.clock = Clock()
        self.base = MemoryStore(clock=self.clock)
        enrol_online(self.base, self.clock, *(machine(h) for h in hosts))
        self.actor = FakeActor(default=lambda inp, ctx: {"n": 1} if ctx.step_id == "s1" else {})
        self.sources = {h: FakeEventSource(name=f"sub@{h}") for h in hosts}
        self.evaluations: list[tuple[str, str, str]] = []
        self.nodes: dict[str, Node] = {}
        for h in hosts:
            self.nodes[h] = self.node(h)

    def node(self, host: str, **kw) -> Node:
        options = dict(
            actors={"*": self.actor},
            event_source=self.sources[host],
            clock=self.clock,
            probe=lambda: ProbeResult(tools={"nvidia-smi": False, "tegrastats": True}),
            load_reader=lambda: {"cpu": 0.1, "mem": 0.2},
            engine_version="test",
            on_evaluated=lambda rule_id, event_id: self.evaluations.append(
                (host, rule_id, event_id)
            ),
        )
        options.update(kw)
        return Node(self.base.peer(), host, **options)

    def define(self, *models) -> None:
        for model in models:
            collection = "rules" if isinstance(model, Rule) else "workflows"
            self.base.put(collection, model.to_dict())

    def publish(self, env) -> None:
        for source in self.sources.values():
            source.publish(dict(env))

    def start(self) -> None:
        for n in self.nodes.values():
            n.start()

    def cycle(self, *hosts: str) -> dict[str, CycleReport]:
        return {h: self.nodes[h].run_once() for h in (hosts or tuple(self.nodes))}

    def run(self, rule_id: str, event_id: str):
        return self.base.get(RUNS_COLLECTION, run_id_for(rule_id, event_id))


# --------------------------------------------------------------------------- heartbeat


def test_start_publishes_a_heartbeat_carrying_the_platform_probe_result():
    c = Cluster("spark")
    beat = c.nodes["spark"].start()
    doc = c.base.get(HEARTBEAT_COLLECTION, "spark")
    assert doc["tools"] == {"nvidia-smi": False, "tegrastats": True}
    assert doc["engine_version"] == "test"
    assert doc["load"] == {"cpu": 0.1, "mem": 0.2}
    assert beat["machine"] == "spark"


def test_heartbeat_is_republished_only_when_due():
    c = Cluster("spark")
    node = c.nodes["spark"]
    node.start()
    assert node.run_once().beat is False
    c.clock.advance(10)
    assert node.run_once().beat is True


def test_probe_gpu_load_lands_in_the_heartbeat_load():
    c = Cluster("spark")
    node = c.node("spark", probe=lambda: ProbeResult(tools={"nvidia-smi": True}, gpu_load=0.5))
    node.start()
    assert c.base.get(HEARTBEAT_COLLECTION, "spark")["load"]["gpu"] == 0.5


# --------------------------------------------------------------------------- one host


def test_one_cycle_ingests_fires_starts_and_drives_the_run_until_idle():
    c = Cluster("spark")
    c.define(one_step_workflow(), event_rule("r", "wf"))
    c.start()
    c.publish(envelope(1))

    report = c.nodes["spark"].run_once()

    assert c.base.get(EVENTS_COLLECTION, "evt_1") is not None
    run = c.run("r", "evt_1")
    assert run is not None, run
    assert run["status"] == "succeeded", run
    assert run["started_by"] == "engine@spark"
    assert step_state(run, ACTION_STEP)["status"] == "succeeded"
    assert report.ingested == 1
    assert report.evaluated == [{"rule": "r", "event": "evt_1"}]
    assert report.started == [run["id"]]
    assert report.transitions > 0
    assert report.errors == []
    assert c.base.get(RULE_FIRES, run["id"]) is None  # intents are keyed by firing key
    (intent,) = c.base.find(RULE_FIRES, {"rule_id": "r"})
    assert intent["status"] == "started"
    assert intent["run_id"] == run["id"]
    # the action saw the trigger
    action_calls = [call for call in c.actor.calls if call[2].step_id == ACTION_STEP]
    assert action_calls
    assert action_calls[0][1]["event"] == "evt_1"
    json.dumps(report.to_dict())  # the report is JSON-serialisable


def test_events_published_before_start_are_not_backfilled_but_later_ones_fire():
    c = Cluster("spark")
    c.define(event_rule("r"))
    c.nodes["spark"].start()
    c.publish(envelope(1))
    c.nodes["spark"].run_once()
    assert c.run("r", "evt_1") is not None  # start pinned the cursors before the event


def test_a_non_matching_event_starts_nothing():
    c = Cluster("spark")
    c.define(event_rule("r"))
    c.start()
    c.publish(envelope(1, type="something.else"))
    report = c.nodes["spark"].run_once()
    assert report.started == []
    assert c.base.find(RUNS_COLLECTION) == []


# --------------------------------------------------------------------------- several hosts


def test_placed_rule_is_evaluated_only_on_its_host():
    c = Cluster("spark", "thor", "spark2")
    c.define(event_rule("on-thor", placement=Placement(machine="thor")))
    c.start()
    c.publish(envelope(1))

    c.cycle("spark", "spark2")
    assert c.evaluations == []
    assert c.run("on-thor", "evt_1") is None
    c.cycle("thor")

    assert c.evaluations == [("thor", "on-thor", "evt_1")]
    run = c.run("on-thor", "evt_1")
    assert run["status"] == "succeeded"
    assert run["started_by"] == "engine@thor"


def test_unplaced_rule_is_evaluated_exactly_once_across_hosts():
    c = Cluster("spark", "thor", "spark2")
    c.define(event_rule("anywhere"))
    c.start()
    for n in range(6):
        c.publish(envelope(n))
        c.cycle(["spark", "thor", "spark2"][n % 3])
    for _ in range(2):
        c.cycle()

    evaluated = sorted(e for h, r, e in c.evaluations if r == "anywhere")
    assert evaluated == [f"evt_{n}" for n in range(6)]
    fires = sorted(d["event_id"] for d in c.base.find(RULE_FIRES, {"rule_id": "anywhere"}))
    assert fires == [f"evt_{n}" for n in range(6)]
    assert all(c.run("anywhere", f"evt_{n}")["status"] == "succeeded" for n in range(6))
    assert len(c.base.find(RUNS_COLLECTION)) == 6


def test_a_run_started_twice_for_one_event_is_a_duplicate_not_a_second_run():
    c = Cluster("spark", "thor")
    c.define(event_rule("anywhere"))
    c.start()
    c.publish(envelope(1))
    c.cycle("spark")
    # thor sees the same pending-or-started intent: it must not start another run
    c.cycle("thor")
    assert len(c.base.find(RUNS_COLLECTION)) == 1


def test_steps_placed_on_other_hosts_are_driven_by_those_hosts():
    c = Cluster("spark", "thor")
    c.define(one_step_workflow(Placement(machine="thor")), event_rule("r", "wf"))
    c.start()
    c.publish(envelope(1))
    c.cycle("spark")
    assert step_state(c.run("r", "evt_1"), "s1")["status"] == "pending"
    c.cycle("thor")
    c.cycle("spark", "thor")
    run = c.run("r", "evt_1")
    assert run["status"] == "succeeded"
    assert step_state(run, "s1")["host"] == "thor"


# --------------------------------------------------------------------------- criterion 5


def test_placed_rule_on_a_drained_host_keeps_its_event_until_undrained():
    c = Cluster("spark", "thor")
    c.define(event_rule("on-thor", placement=Placement(machine="thor")))
    c.start()
    Containment(c.base, clock=c.clock).drain("thor", "ops@test")
    c.publish(envelope(1))

    report = c.cycle()["thor"]
    assert c.run("on-thor", "evt_1") is None
    assert c.evaluations == []
    assert report.deferred
    assert report.deferred[0]["event"] == "evt_1"
    assert "drained" in report.deferred[0]["reason"]
    assert report.errors == []
    c.cycle()  # still drained: still kept, never silently consumed
    assert c.run("on-thor", "evt_1") is None

    Containment(c.base, clock=c.clock).undrain("thor", "ops@test")
    c.cycle()
    assert c.evaluations == [("thor", "on-thor", "evt_1")]
    assert c.run("on-thor", "evt_1")["status"] == "succeeded"


def test_placed_rule_on_a_host_seen_offline_keeps_its_event_until_it_beats_again():
    c = Cluster("spark", "thor")
    thor = c.nodes["thor"] = c.node("thor", beat_every=3600)
    c.define(event_rule("on-thor", placement=Placement(machine="thor")))
    c.start()
    c.clock.advance(31)  # thor's heartbeat is now stale: placement sees it offline
    c.nodes["spark"].beat()
    c.publish(envelope(1))

    report = c.cycle()["thor"]
    assert c.run("on-thor", "evt_1") is None
    assert "offline" in report.deferred[0]["reason"]
    assert c.evaluations == []  # spark did not take thor's rule either

    thor.beat()
    c.cycle()
    assert c.evaluations == [("thor", "on-thor", "evt_1")]
    assert c.run("on-thor", "evt_1")["status"] == "succeeded"


def test_a_deferred_event_does_not_block_the_unplaced_rules():
    c = Cluster("thor")
    c.define(event_rule("on-thor", placement=Placement(machine="thor")), event_rule("free"))
    c.start()
    Containment(c.base, clock=c.clock).drain("thor", "ops@test")
    c.publish(envelope(1))
    c.cycle()
    assert c.run("free", "evt_1") is not None
    assert c.run("on-thor", "evt_1") is None


# --------------------------------------------------------------------------- actors


def test_stored_actor_definitions_are_wired_through_limits_and_released_on_deliver():
    c = Cluster("spark")
    bot = Actor(id="bot", name="bot", kind="agent", machine="spark", params={"max_concurrency": 1})
    c.base.put("actors", bot.to_dict())
    accepting = FakeActor().on("s1", ("accept",))
    made: list[str] = []

    def factory(actor: Actor):
        made.append(actor.id)
        return accepting

    node = c.nodes["spark"] = c.node("spark", adapters={"agent": factory})
    c.define(one_step_workflow(Placement(actor="bot")), event_rule("r", "wf"))
    node.start()
    c.publish(envelope(1))
    node.run_once()

    run = c.run("r", "evt_1")
    assert step_state(run, "s1")["status"] == "waiting"
    assert made == ["bot"]
    key = idempotency_key(run["id"], "s1")
    usage = c.base.get("actor_usage", "bot")
    assert [s["key"] for s in usage["inflight"]] == [key]  # LimitedActor holds the slot

    assert node.deliver(key, InvocationResult.completed({"n": 5, "tokens": 7})) is True
    usage = c.base.get("actor_usage", "bot")
    assert usage["inflight"] == []
    assert usage["tokens"] == 7  # released on deliver
    node.run_once()
    assert c.run("r", "evt_1")["status"] == "succeeded"
    assert made == ["bot"]  # the adapter is cached, not rebuilt per call


def test_an_unknown_actor_kind_falls_back_to_the_injected_ports():
    c = Cluster("spark")
    robot = Actor(id="arm", name="arm", kind="robot", machine="spark")
    c.base.put("actors", robot.to_dict())
    c.define(one_step_workflow(Placement(actor="arm")), event_rule("r", "wf"))
    c.start()
    c.publish(envelope(1))
    c.cycle()
    assert c.run("r", "evt_1")["status"] == "succeeded"
    assert c.actor.calls_for("s1")


# --------------------------------------------------------------------------- reporter / logs


class Poster:
    def __init__(self) -> None:
        self.posts: list[tuple[str, str]] = []

    def post(self, channel: str, text: str) -> None:
        self.posts.append((channel, text))


def test_finished_runs_are_reported_once_by_the_node_that_started_them():
    c = Cluster("spark", "thor")
    posters = {h: Poster() for h in ("spark", "thor")}
    for h in posters:
        c.nodes[h] = c.node(h, reporter=RunReporter(posters[h], channel="#rules"))
    c.define(event_rule("on-thor", placement=Placement(machine="thor")))
    c.start()
    c.publish(envelope(1))
    c.cycle()
    c.cycle()
    assert posters["spark"].posts == []
    assert len(posters["thor"].posts) == 1
    channel, text = posters["thor"].posts[0]
    assert channel == "#rules"
    assert "succeeded" in text


def test_cycle_logs_are_json_with_host_and_run_context():
    c = Cluster("spark")
    c.define(event_rule("r"))
    c.start()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(host="unused"))
    logger = logging.getLogger("culture_rules.node")
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    try:
        c.publish(envelope(1))
        c.cycle()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    started = [ln for ln in lines if ln["run_id"] == run_id_for("r", "evt_1")]
    assert started
    assert all(ln["host"] == "spark" for ln in started)


# --------------------------------------------------------------------------- loop


class _Flaky:
    """A store handle whose ``find`` raises a transient error the first ``n`` times."""

    def __init__(self, inner, n: int) -> None:
        self._inner = inner
        self.n = n

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def find(self, *a, **kw):
        if self.n > 0:
            self.n -= 1
            raise TransientStoreError("write conflict; retry")
        return self._inner.find(*a, **kw)


def test_a_transient_store_error_is_recorded_and_the_next_cycle_recovers():
    c = Cluster("spark")
    c.define(event_rule("r"))
    node = Node(
        _Flaky(c.base.peer(), 0),
        "spark",
        actors={"*": c.actor},
        event_source=c.sources["spark"],
        clock=c.clock,
        probe=lambda: ProbeResult(),
        engine_version="test",
    )
    node.start()
    node._store.n = 1  # the next cycle's first find fails transiently
    c.publish(envelope(1))
    first = node.run_once()
    assert any("TransientStoreError" in e for e in first.errors)
    for _ in range(2):
        node.run_once()
    assert c.run("r", "evt_1")["status"] == "succeeded"


def test_run_loops_until_stop_and_stops_gracefully():
    c = Cluster("spark")
    c.define(event_rule("r"))
    node = c.nodes["spark"]
    cycles: list[int] = []
    thread = threading.Thread(target=lambda: cycles.append(node.run(idle=0.01)), daemon=True)
    thread.start()
    c.publish(envelope(1))
    for _ in range(500):
        if c.run("r", "evt_1") is not None and c.run("r", "evt_1")["status"] == "succeeded":
            break
        threading.Event().wait(0.01)
    node.stop()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert cycles
    assert cycles[0] >= 1
    assert c.run("r", "evt_1")["status"] == "succeeded"


def test_run_with_max_cycles_returns_the_cycle_count():
    c = Cluster("spark")
    assert c.nodes["spark"].run(idle=0.0, max_cycles=3) == 3


def test_node_rejects_an_empty_host():
    with pytest.raises(ValueError):
        Node(MemoryStore(), "", actors={})
