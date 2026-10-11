"""t25: ``default_ports`` registers a port for every action kind (extras degrade loudly)."""

from __future__ import annotations

import importlib.util

import pytest

from culture_rules.engine.actorport import FAILED
from culture_rules.engine.runs import ACTION_STEP, step_state
from culture_rules.machines.probe import ProbeResult
from culture_rules.model.action import Action
from culture_rules.model.action_kinds import ACTION_KINDS
from culture_rules.model.actor import Actor
from culture_rules.model.rule import Rule, Trigger
from culture_rules.node import runner
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.node.daemon import HeartbeatOptions, Node
from culture_rules.node.firing import run_id_for
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, enrol_online, machine
from tests.events.fakes import FakeEventSource, envelope

BEATS = HeartbeatOptions(
    probe=lambda: ProbeResult(tools={}),
    load_reader=lambda: {"cpu": 0.1, "mem": 0.2},
    engine_version="test",
)


# #53 t8 registers these ports; remove this set then.
PENDING_PORTS_T8 = frozenset({"email.send", "search.web", "llm.complete", "library.push"})


def test_every_action_kind_has_a_registered_port() -> None:
    ports = runner.default_ports(MemoryStore(), "spark")
    for kind in set(ACTION_KINDS) - PENDING_PORTS_T8:
        assert f"action:{kind}" in ports, kind
    assert "action:mesh.message" in ports  # legacy alias of message


def test_pending_ports_are_catalogued() -> None:
    assert PENDING_PORTS_T8 <= set(ACTION_KINDS)


@pytest.mark.parametrize("kind", sorted(PENDING_PORTS_T8))
def test_pending_port_exemption_is_still_needed(kind: str) -> None:
    """Fails once t8 registers the port: drop the kind from ``PENDING_PORTS_T8`` then."""
    ports = runner.default_ports(MemoryStore(), "spark")
    assert f"action:{kind}" not in ports, f"{kind} is registered now: remove the exemption"


def _run_rule(action, actor):
    clock = Clock()
    base = MemoryStore(clock=clock)
    enrol_online(base, clock, machine("spark"))
    base.put(ACTORS_COLLECTION, actor.to_dict())
    rule = Rule(
        id="r",
        name="r",
        trigger=Trigger(kind="event", params={"type": "task.requested"}),
        action=action,
    )
    base.put("rules", rule.to_dict())
    source = FakeEventSource(name="sub@spark")
    node = Node(
        base.peer(),
        "spark",
        actors=runner.default_ports(base, "spark"),
        event_source=source,
        clock=clock,
        heartbeat_options=BEATS,
    )
    node.start()
    source.publish(dict(envelope(1)))
    node.run_once()
    return base.get("runs", run_id_for("r", "evt_1"))


def test_github_comment_without_the_extra_fails_extra_missing(monkeypatch) -> None:
    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "cryptography" else real(name, *a, **k),
    )
    actor = Actor(id="gh", name="gh", kind="app", params={"surface": "github"})
    action = Action(
        kind="github.comment",
        params={"actor": "gh", "repo": "o/r", "number": 1, "body": "hi"},
    )
    run = _run_rule(action, actor)
    assert run["status"] == "failed", run
    state = step_state(run, ACTION_STEP)
    assert state["status"] == FAILED or state["status"] == "failed", state
    assert "extra_missing" in str(state.get("error")), state
    assert "no_actor_port" not in str(run)


def test_machine_command_runs_end_to_end_through_the_registered_port() -> None:
    actor = Actor(
        id="box",
        name="box",
        kind="runner",
        machine="spark",
        params={"commands": {"say": {"argv": ["echo", "hello"], "params": {}, "timeout": 10}}},
    )
    action = Action(kind="machine.command", params={"actor": "box", "command": "say"})
    run = _run_rule(action, actor)
    assert run["status"] == "succeeded", run
    assert step_state(run, ACTION_STEP)["outputs"]["stdout"].strip() == "hello"
