"""Criterion 2 of t41: ``culture-rules node run --once --host <name>`` (one cycle, exit 0)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from culture_rules.cli import main
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION
from culture_rules.model.action import Action
from culture_rules.model.rule import Rule, Trigger
from culture_rules.node import runner
from culture_rules.store.memory import MemoryStore
from tests.events.fakes import FakeEventSource, envelope

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def store(monkeypatch):
    base = MemoryStore()
    base.put(
        "rules",
        Rule(
            id="r",
            name="r",
            trigger=Trigger(kind="event", params={"type": "task.requested"}),
            action=Action(kind="noop"),
        ).to_dict(),
    )
    monkeypatch.setattr(runner, "open_store", lambda: base)
    monkeypatch.setattr(runner, "default_ports", lambda store, host: {})
    # keep the root logger and the event bus out of CLI tests (no handler leaks, no broker)
    monkeypatch.setattr(runner, "configure_logging", lambda **kw: None)
    monkeypatch.setattr(runner, "open_emitter", lambda store, host: None)
    return base


def test_node_run_once_performs_one_cycle_and_exits_zero(store, monkeypatch, capsys):
    monkeypatch.setattr(runner, "open_event_source", lambda host: None)
    assert main(["node", "run", "--once", "--host", "spark"]) == 0
    assert store.get(HEARTBEAT_COLLECTION, "spark") is not None
    out = capsys.readouterr()
    assert "spark" in out.out


def test_node_run_once_json_reports_what_it_did(store, monkeypatch, capsys):
    source = FakeEventSource(name="sub")
    monkeypatch.setattr(runner, "open_event_source", lambda host: source)
    # first cycle pins the cursors; an event published afterwards fires on the next run
    assert main(["node", "run", "--once", "--host", "spark", "--json"]) == 0
    capsys.readouterr()
    source.publish(envelope(1))
    assert main(["node", "run", "--once", "--host", "spark", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["host"] == "spark" and payload["cycles"] == 1
    cycle = payload["report"]
    assert cycle["ingested"] == 1
    assert cycle["evaluated"] == [{"rule": "r", "event": "evt_1"}]
    assert len(cycle["started"]) == 1
    assert cycle["errors"] == []
    assert store.get(RUNS_COLLECTION, cycle["started"][0]) is not None


def test_node_run_defaults_the_host_to_this_machine(store, monkeypatch, capsys):
    monkeypatch.setattr(runner, "open_event_source", lambda host: None)
    monkeypatch.setattr(runner, "default_host", lambda: "here")
    assert main(["node", "run", "--once", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["host"] == "here"


def test_node_run_without_a_store_is_an_environment_error(monkeypatch, capsys):
    monkeypatch.delenv("CULTURE_RULES_MONGO_URI", raising=False)
    assert main(["node", "run", "--once", "--host", "spark", "--json"]) == 2
    err = json.loads(capsys.readouterr().err)
    assert "CULTURE_RULES_MONGO_URI" in err["message"]


def test_the_node_command_imports_the_engine_only_lazily():
    path = ROOT / "culture_rules" / "cli" / "_commands" / "node.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top = []
    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom) and stmt.module:
            top.append(stmt.module)
        elif isinstance(stmt, ast.Import):
            top += [a.name for a in stmt.names]
    forbidden = ("culture_rules.node", "culture_rules.store", "culture_rules.engine")
    assert not [m for m in top if m.startswith(forbidden)]


def test_node_run_is_in_learn_and_explain(capsys):
    assert main(["learn", "--json"]) == 0
    listed = {tuple(c["path"]) for c in json.loads(capsys.readouterr().out)["commands"]}
    assert ("node", "run") in listed
    assert main(["explain", "node", "run"]) == 0
    assert "--once" in capsys.readouterr().out
