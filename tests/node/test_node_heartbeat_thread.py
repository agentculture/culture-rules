"""Node.run heartbeats from a thread, so a long synchronous step never takes the host
offline - to its peers, to Statistics, or to its own executor's placement."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.runs import step_state
from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION, online_machines
from culture_rules.model.placement import Placement
from tests.engine.run_helpers import T0, edge, port, step, workflow
from tests.events.fakes import envelope
from tests.node.test_node import BEATS, Cluster, event_rule

BEAT_EVERY = 0.02


def fmt(moment) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def wait_for(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def beat_threads() -> set[threading.Thread]:
    return {t for t in threading.enumerate() if t.name.startswith("heartbeat-")}


def test_heartbeats_keep_advancing_while_a_step_blocks_the_drive():
    before = beat_threads()
    c = Cluster("spark", "thor")
    stamps: list[str] = []
    online: list[set[str]] = []

    class Blocking:
        supports_idempotency_key = True

        def invoke(self, payload, key, deadline, *, context):
            if context.step_id != "s1":
                return InvocationResult.completed({})
            for _ in range(4):  # 40 s of fake time: more than 3 beat intervals (offline)
                c.clock.advance(10)
                want = fmt(c.clock())
                wait_for(lambda want=want: c.base.get(HEARTBEAT_COLLECTION, "spark")["ts"] == want)
                stamps.append(c.base.get(HEARTBEAT_COLLECTION, "spark")["ts"])
                online.append(online_machines(c.base, c.clock()))
            return InvocationResult.completed({"n": 1})

    wf = workflow(
        (
            step("s1", outputs=(port("n", "integer"),)),
            step("s2", inputs=(port("n", "integer"),), placement=Placement(machine="spark")),
        ),
        (edge("s1", "n", "s2", "n"),),
    )
    node = c.node(
        "spark",
        actors={"*": Blocking()},
        heartbeat_options=replace(BEATS, beat_every=BEAT_EVERY),
    )
    c.define(wf, event_rule("r", "wf", placement=Placement(machine="spark")))
    c.publish(envelope(1))
    runner = threading.Thread(target=lambda: node.run(idle=0.01), daemon=True)
    runner.start()
    try:
        assert wait_for(lambda: (c.run("r", "evt_1") or {}).get("status") == "succeeded")
    finally:
        node.stop()
        runner.join(timeout=10)

    assert stamps == [fmt(T0 + timedelta(seconds=s)) for s in (10, 20, 30, 40)]
    assert all("spark" in seen for seen in online)
    run = c.run("r", "evt_1")
    assert [h["event"] for h in run["history"] if h["step"] == "s2"] == [
        "dispatched",
        "succeeded",
    ]  # never placement_waiting on machine_offline
    assert step_state(run, "s2")["host"] == "spark"
    assert not runner.is_alive()
    assert beat_threads() - before == set()  # the heartbeat thread stopped with the node


def test_run_once_beats_once_and_starts_no_heartbeat_thread():
    before = beat_threads()
    c = Cluster("spark")
    node = c.node("spark", heartbeat_options=replace(BEATS, beat_every=BEAT_EVERY))
    report = node.run_once()
    assert c.base.get(HEARTBEAT_COLLECTION, "spark")["ts"] == fmt(c.clock())
    assert report.errors == []
    assert beat_threads() - before == set()


def test_the_heartbeat_thread_stops_when_run_dies():
    before = beat_threads()
    c = Cluster("spark")

    class Died(BaseException):
        pass

    node = c.node("spark", heartbeat_options=replace(BEATS, beat_every=BEAT_EVERY))
    beats: list[str] = []
    original = node.beat

    def beat():
        beats.append("beat")
        return original()

    node.beat = beat  # type: ignore[method-assign]

    def die(report):
        wait_for(lambda: len(beats) >= 3)  # the thread is beating meanwhile
        raise Died

    node._drive = die  # type: ignore[method-assign]
    raised: list[BaseException] = []

    def run() -> None:
        try:
            node.run(idle=0.01)
        except Died as exc:
            raised.append(exc)

    runner = threading.Thread(target=run, daemon=True)
    runner.start()
    runner.join(timeout=10)
    assert len(raised) == 1
    assert len(beats) >= 3
    assert beat_threads() - before == set()
