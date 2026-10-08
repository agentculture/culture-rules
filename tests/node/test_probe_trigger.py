"""t20: probe triggers run an allow-listed command on a cron schedule and fire on change
or on a condition over its output (exactly once per rule/slot)."""

from __future__ import annotations

from datetime import UTC, datetime

from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.machines.probe import ProbeResult
from culture_rules.model.action import Action
from culture_rules.model.actor import Actor
from culture_rules.model.rule import Rule, Trigger
from culture_rules.node.daemon import HeartbeatOptions, Node
from culture_rules.node.probe_trigger import PROBE_STATE, probe_event_id
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, FakeActor, enrol_online, machine

START = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)
BEATS = HeartbeatOptions(
    probe=lambda: ProbeResult(tools={}), load_reader=lambda: {}, engine_version="test"
)
HOT = {"op": "compare", "cmp": ">", "left": {"field": "data.temp"}, "right": {"literal": 30}}


def probe_rule(id="p", mode="change", condition=None, actor="sensor", **extra) -> Rule:
    params = {
        "actor": actor,
        "command": "read",
        "schedule": "*/5 * * * *",
        "mode": mode,
        **extra,
    }
    return Rule(
        id=id,
        name=id,
        trigger=Trigger(kind="probe", params=params),
        condition=condition,
        action=Action(kind="noop", params={}),
    )


class Mesh:
    def __init__(self, tmp_path, *hosts: str, machine_of="spark", argv=None) -> None:
        self.clock = Clock(START)
        self.base = MemoryStore(clock=self.clock)
        enrol_online(self.base, self.clock, *(machine(h) for h in hosts))
        self.file = tmp_path / "out.txt"
        self.file.write_text('{"temp": 20}')
        argv = argv or ["cat", str(self.file)]
        self.base.put(
            "actors",
            Actor(
                id="sensor",
                name="sensor",
                kind="runner",
                machine=machine_of,
                params={"commands": {"read": {"argv": argv, "timeout": 5}}},
            ).to_dict(),
        )
        self.nodes = {h: self.node(h) for h in hosts}

    def node(self, host: str) -> Node:
        return Node(
            self.base.peer(),
            host,
            actors={"*": FakeActor()},
            clock=self.clock,
            heartbeat_options=BEATS,
        )

    def set_output(self, text: str) -> None:
        self.file.write_text(text)

    def define(self, *rules: Rule) -> None:
        for r in rules:
            self.base.put("rules", r.to_dict())

    def start(self) -> None:
        for n in self.nodes.values():
            n.start()

    def step(self, minutes: int = 5) -> None:
        """Advance the clock and cycle every node once."""
        self.clock.advance(minutes * 60)
        for n in self.nodes.values():
            report = n.run_once()
            assert report.errors == [], report.errors

    def runs(self, rule_id: str) -> list[dict]:
        return [r for r in self.base.find(RUNS_COLLECTION) if r["rule"]["id"] == rule_id]

    def slots(self, rule_id: str) -> list[str]:
        return sorted(r["trigger"]["data"]["slot"] for r in self.runs(rule_id))

    def events(self) -> list[dict]:
        """The events the probes emitted (not the runs' own rules.run.* events, d21)."""
        return [
            e
            for e in self.base.find(EVENTS_COLLECTION)
            if not str(e["envelope"].get("type", "")).startswith("rules.run.")
        ]


def test_change_mode_fires_on_first_and_changed_outputs_only(tmp_path):
    mesh = Mesh(tmp_path, "spark")
    mesh.define(probe_rule())
    mesh.start()
    mesh.step()  # 10:05 first output
    mesh.step()  # 10:10 unchanged
    mesh.step()  # 10:15 unchanged
    mesh.set_output('{"temp": 21}')
    mesh.step()  # 10:20 changed
    mesh.step()  # 10:25 unchanged
    mesh.set_output("plain text")
    mesh.step()  # 10:30 changed (not JSON)
    assert mesh.slots("p") == [
        "2026-10-04T10:05:00+00:00",
        "2026-10-04T10:20:00+00:00",
        "2026-10-04T10:30:00+00:00",
    ]
    assert mesh.base.get(PROBE_STATE, "p")["digest"]


def test_event_shape_json_output_merged_into_data(tmp_path):
    mesh = Mesh(tmp_path, "spark")
    mesh.define(probe_rule())
    mesh.start()
    mesh.step()
    slot = "2026-10-04T10:05:00+00:00"
    doc = mesh.base.get(EVENTS_COLLECTION, probe_event_id("p", slot))
    env = doc["envelope"]
    assert env["kind"] == "probe"
    assert env["data"]["temp"] == 20
    assert env["data"]["exit_code"] == 0
    assert env["data"]["rule_id"] == "p"
    assert env["data"]["slot"] == slot
    assert "temp" in env["data"]["stdout"]


def test_condition_mode_fires_only_when_temp_exceeds_30(tmp_path):
    mesh = Mesh(tmp_path, "spark")
    mesh.define(probe_rule(mode="condition", condition=HOT))
    mesh.start()
    mesh.step()  # 20: no
    mesh.set_output('{"temp": 31}')
    mesh.step()  # 31: yes
    mesh.step()  # 31 again: yes (condition mode is level, not edge)
    mesh.set_output('{"temp": 30}')
    mesh.step()  # 30: no
    assert mesh.slots("p") == ["2026-10-04T10:10:00+00:00", "2026-10-04T10:15:00+00:00"]


def test_a_failing_command_emits_nothing_and_does_not_record_state(tmp_path):
    mesh = Mesh(tmp_path, "spark", argv=["false"])
    mesh.define(probe_rule())
    mesh.start()
    mesh.step()
    mesh.step()
    assert mesh.events() == []
    assert mesh.base.get(PROBE_STATE, "p") is None


def test_unknown_command_or_missing_actor_emits_nothing(tmp_path):
    mesh = Mesh(tmp_path, "spark")
    mesh.define(probe_rule("a", actor="nobody"), probe_rule("b", command="missing"))
    mesh.start()
    mesh.step()
    assert mesh.events() == []


def test_a_probe_for_an_actor_on_another_machine_runs_nowhere_here(tmp_path):
    mesh = Mesh(tmp_path, "spark", "thor", machine_of="thor")
    mesh.define(probe_rule())
    mesh.nodes.pop("thor")  # thor never cycles
    mesh.start()
    mesh.step()
    mesh.step()
    assert mesh.events() == []


def test_a_second_process_on_the_owner_host_is_absorbed_by_the_slot_marker(tmp_path):
    mesh = Mesh(tmp_path, "spark", "thor")
    mesh.nodes["zombie"] = mesh.node("spark")
    mesh.define(probe_rule(mode="condition"))
    mesh.start()
    mesh.step()
    mesh.step()
    assert len(mesh.events()) == 2  # thor does not own the actor's machine
    assert len(mesh.runs("p")) == 2


def test_each_probe_event_targets_only_its_own_rule(tmp_path):
    mesh = Mesh(tmp_path, "spark")
    mesh.define(probe_rule("one", mode="condition"), probe_rule("two", mode="condition"))
    mesh.start()
    mesh.step()
    for rid in ("one", "two"):
        runs = mesh.runs(rid)
        assert len(runs) == 1
        assert runs[0]["trigger"]["data"]["rule_id"] == rid


def test_disabled_rules_and_disabled_actors_run_nothing(tmp_path):
    mesh = Mesh(tmp_path, "spark")
    mesh.define(probe_rule("off"))
    doc = mesh.base.get("rules", "off")
    mesh.base.put("rules", {**doc, "enabled": False})
    mesh.start()
    mesh.step()
    assert mesh.events() == []
    mesh.define(probe_rule("on"))
    actor = mesh.base.get("actors", "sensor")
    mesh.base.put("actors", {**actor, "enabled": False})
    mesh.step()
    assert mesh.events() == []


def test_no_backfill_before_start(tmp_path):
    mesh = Mesh(tmp_path, "spark")
    mesh.define(probe_rule())
    mesh.clock.advance(3600)  # slots before start never fire
    mesh.start()
    assert mesh.events() == []


def test_slots_are_not_run_before_they_are_due(tmp_path):
    mesh = Mesh(tmp_path, "spark")
    mesh.define(probe_rule())
    mesh.start()
    mesh.step(4)
    assert mesh.events() == []


def test_an_unparseable_or_non_string_schedule_probe_is_skipped(tmp_path):
    mesh = Mesh(tmp_path, "spark")
    bad = probe_rule("bad").to_dict()
    bad["trigger"]["params"]["schedule"] = None
    mesh.define(probe_rule("good"))
    mesh.base.put("rules", bad)
    mesh.base.put("rules", {**probe_rule("broken").to_dict(), "action": "not an object"})
    mesh.start()
    mesh.step()
    assert mesh.slots("good") != []
    assert mesh.runs("bad") == []
    assert mesh.runs("broken") == []
