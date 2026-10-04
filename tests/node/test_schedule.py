"""t19: schedule triggers fire once per cron slot on the placed host.

Nodes share one MemoryStore (``peer`` handles stand in for hosts) and a fake clock that is
advanced over one hour in 10 s cycles. A ``*/5`` rule therefore has 12 slots in
``(10:00, 11:00]``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.machines.probe import ProbeResult
from culture_rules.model.action import Action
from culture_rules.model.placement import Placement
from culture_rules.model.rule import Rule, Trigger
from culture_rules.node.daemon import HeartbeatOptions, Node
from culture_rules.node.firing import RULE_FIRES
from culture_rules.node.schedule import Scheduler, schedule_event_id
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, FakeActor, enrol_online, machine

START = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
END = START + timedelta(hours=1)
STEP_S = 10

BEATS = HeartbeatOptions(
    probe=lambda: ProbeResult(tools={}),
    load_reader=lambda: {},
    engine_version="test",
)


def schedule_rule(id: str, cron: str = "*/5 * * * *", placement=None, **params) -> Rule:
    return Rule(
        id=id,
        name=id,
        trigger=Trigger(kind="schedule", params={"cron": cron, **params}),
        action=Action(kind="noop", params={}),
        placement=placement,
    )


class Mesh:
    def __init__(self, *hosts: str) -> None:
        self.clock = Clock(START)
        self.base = MemoryStore(clock=self.clock)
        enrol_online(self.base, self.clock, *(machine(h) for h in hosts))
        self.actor = FakeActor(default=lambda inp, ctx: {})
        self.nodes = {h: self.node(h) for h in hosts}

    def node(self, host: str) -> Node:
        return Node(
            self.base.peer(),
            host,
            actors={"*": self.actor},
            clock=self.clock,
            heartbeat_options=BEATS,
        )

    def define(self, *rules: Rule) -> None:
        for rule in rules:
            self.base.put("rules", rule.to_dict())

    def start(self) -> None:
        for n in self.nodes.values():
            n.start()

    def cycle(self, *hosts: str) -> None:
        for h in hosts or tuple(self.nodes):
            report = self.nodes[h].run_once()
            assert report.errors == [], report.errors

    def run_until(self, end: datetime, *hosts: str) -> None:
        while self.clock() < end:
            self.clock.advance(STEP_S)
            self.cycle(*hosts)

    def runs(self, rule_id: str) -> list[dict]:
        return [r for r in self.base.find(RUNS_COLLECTION) if r["rule"]["id"] == rule_id]

    def slots(self, rule_id: str) -> list[str]:
        return sorted(r["trigger"]["data"]["slot"] for r in self.runs(rule_id))


def expected_slots(first: int, last: int) -> list[str]:
    """ISO UTC instants of the */5 slots 10:<first> .. 10:<last> (60 = 11:00)."""
    return [
        (START + timedelta(minutes=m)).isoformat(timespec="seconds")
        for m in range(first, last + 1, 5)
    ]


def test_every_five_minutes_fires_twelve_runs_on_the_placed_host_and_none_elsewhere():
    mesh = Mesh("spark", "thor")
    mesh.define(schedule_rule("tick", placement=Placement(machine="spark")))
    mesh.start()
    mesh.run_until(END)

    runs = mesh.runs("tick")
    assert len(runs) == 12
    assert mesh.slots("tick") == expected_slots(5, 60)
    assert {r["started_by"] for r in runs} == {"engine@spark"}
    assert {i["host"] for i in mesh.base.find(RULE_FIRES)} == {"spark"}
    assert all(r["status"] == "succeeded" for r in runs)


def test_a_rule_placed_on_another_host_is_not_synthesized_here():
    mesh = Mesh("spark", "thor")
    mesh.define(schedule_rule("tick", placement=Placement(machine="thor")))
    mesh.start()
    mesh.run_until(START + timedelta(minutes=10), "spark")  # thor never cycles
    assert mesh.base.find(EVENTS_COLLECTION) == []
    assert mesh.runs("tick") == []


def test_an_unplaced_rule_fires_once_per_slot_mesh_wide_while_two_hosts_race():
    mesh = Mesh("spark", "thor")
    mesh.define(schedule_rule("tick"))
    mesh.start()
    mesh.run_until(END)  # both hosts tick every slot

    assert mesh.slots("tick") == expected_slots(5, 60)
    assert len(mesh.base.find(RULE_FIRES)) == 12


def test_two_processes_on_the_placed_host_racing_the_same_slot_start_one_run():
    mesh = Mesh("spark", "thor")
    mesh.define(schedule_rule("tick", placement=Placement(machine="spark")))
    mesh.nodes["zombie"] = mesh.node("spark")  # a second process for the same host
    mesh.start()
    mesh.run_until(END)

    assert mesh.slots("tick") == expected_slots(5, 60)


def test_a_node_restart_mid_hour_neither_refires_nor_duplicates():
    mesh = Mesh("spark", "thor")
    mesh.define(schedule_rule("tick", placement=Placement(machine="spark")))
    mesh.start()
    mesh.run_until(START + timedelta(minutes=32))
    mesh.nodes["spark"] = mesh.node("spark")  # restart: a fresh process, same host
    mesh.nodes["spark"].start()
    mesh.run_until(END)

    assert mesh.slots("tick") == expected_slots(5, 60)


def test_a_restart_that_replays_an_already_fired_slot_does_not_duplicate_it():
    mesh = Mesh("spark")
    mesh.define(schedule_rule("tick", placement=Placement(machine="spark")))
    mesh.start()
    mesh.run_until(START + timedelta(minutes=5, seconds=30))
    assert mesh.slots("tick") == expected_slots(5, 5)
    # a process that came up before 10:05 (its window still covers the slot) ticks late
    late = Scheduler(mesh.base.peer(), "spark", mesh.nodes["spark"].firing, clock=mesh.clock)
    late.last_tick = START + timedelta(minutes=4)
    assert late.tick() == []
    mesh.cycle()
    assert mesh.slots("tick") == expected_slots(5, 5)


def test_slots_missed_while_the_node_was_down_are_not_backfilled():
    mesh = Mesh("spark", "thor")
    mesh.define(schedule_rule("tick", placement=Placement(machine="spark")))
    mesh.start()
    mesh.run_until(START + timedelta(minutes=31))
    mesh.run_until(START + timedelta(minutes=47), "thor")  # spark is down 10:31-10:47
    mesh.nodes["spark"] = mesh.node("spark")
    mesh.nodes["spark"].start()
    mesh.run_until(END)

    assert mesh.slots("tick") == expected_slots(5, 30) + expected_slots(50, 60)


def test_each_schedule_event_targets_only_its_own_rule():
    mesh = Mesh("spark")
    mesh.define(schedule_rule("five"), schedule_rule("ten", "*/10 * * * *"))
    mesh.start()
    mesh.run_until(END)

    assert mesh.slots("five") == expected_slots(5, 60)
    assert mesh.slots("ten") == expected_slots(10, 60)[::2]
    for rule_id in ("five", "ten"):
        assert {r["trigger"]["data"]["rule_id"] for r in mesh.runs(rule_id)} == {rule_id}


def test_schedule_event_shape_and_marker_id():
    mesh = Mesh("spark")
    mesh.define(schedule_rule("tick", placement=Placement(machine="spark")))
    mesh.start()
    mesh.run_until(START + timedelta(minutes=5))
    slot = (START + timedelta(minutes=5)).isoformat(timespec="seconds")
    doc = mesh.base.get(EVENTS_COLLECTION, schedule_event_id("tick", slot))
    assert doc is not None
    env = doc["envelope"]
    assert env["kind"] == "schedule"
    assert env["data"] == {"rule_id": "tick", "slot": slot, "cron": "*/5 * * * *", "tz": None}
    assert doc["host"] == "spark"


def test_a_bad_cron_is_skipped_without_failing_the_stage_or_other_rules():
    mesh = Mesh("spark")
    mesh.define(schedule_rule("bad", "not a cron"), schedule_rule("tick"))
    mesh.start()
    mesh.run_until(START + timedelta(minutes=10))
    assert mesh.slots("tick") == expected_slots(5, 10)
    assert mesh.runs("bad") == []


def test_disabled_and_deleted_rules_do_not_synthesize_events():
    mesh = Mesh("spark")
    off = schedule_rule("off")
    off_doc = {**off.to_dict(), "enabled": False}
    gone_doc = {**schedule_rule("gone").to_dict(), "deleted_at": "2026-10-04T09:00:00Z"}
    mesh.base.put("rules", off_doc)
    mesh.base.put("rules", gone_doc)
    mesh.start()
    mesh.run_until(START + timedelta(minutes=10))
    assert mesh.base.find(EVENTS_COLLECTION) == []
