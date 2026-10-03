"""R2: the production node (``run_node``) wires human asks, run reports and JSON logs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.actors.human import ASK_REQUESTED, HumanAdapter
from culture_rules.engine.actorport import ACCEPTED, InvocationContext
from culture_rules.engine.reports import RunReporter
from culture_rules.events import events_cli_adapter
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.events.source import EventFabricError
from culture_rules.model.actor import Actor
from culture_rules.node import daemon, runner
from culture_rules.store.memory import MemoryStore


@pytest.fixture
def node_rig(monkeypatch):
    """run_node over a MemoryStore with no event bus; captures the Node it builds."""
    store = MemoryStore()
    store.put("actors", Actor(id="alice", name="Alice", kind="human").to_dict())
    captured: dict = {}

    class SpyNode(daemon.Node):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            captured["node"] = self
            captured["kwargs"] = kwargs

    def no_events_cli():
        raise EventFabricError("events-cli is not available (test)")

    monkeypatch.setattr(daemon, "Node", SpyNode)
    monkeypatch.setattr(runner, "open_store", lambda: store)
    monkeypatch.setattr(runner, "open_event_source", lambda host: None)
    monkeypatch.setattr(runner, "_events_cli_client", no_events_cli)
    monkeypatch.setattr(runner, "configure_logging", lambda **kw: None)
    monkeypatch.delenv("CULTURE_RULES_REPORT_CHANNEL", raising=False)
    return store, captured


def _human_ctx() -> InvocationContext:
    return InvocationContext(
        "run-1", "ask", "actor_task", "spark", 1, "alice", {"question": "Ship it?"}
    )


def test_a_human_step_routed_by_the_production_router_gets_a_human_adapter(node_rig):
    store, captured = node_rig
    runner.run_node("spark", once=True)
    node = captured["node"]
    port = node.router(_human_ctx())
    assert port is not None, "a human actor_task step must not fail with no_actor_port"
    assert isinstance(port.inner, HumanAdapter)
    deadline = datetime.now(UTC) + timedelta(hours=1)
    result = port.invoke({}, "ik-ask-1", deadline, context=_human_ctx())
    assert result.outcome == ACCEPTED
    # no event bus: the ask request is recorded into the store's events collection
    events = [d["envelope"] for d in store.find(EVENTS_COLLECTION)]
    assert [e["type"] for e in events] == [ASK_REQUESTED]
    assert events[0]["data"]["question"] == "Ship it?"
    assert events[0]["source"].startswith("app://culture-rules/")


def test_open_emitter_prefers_events_cli_when_available(monkeypatch):
    published = []

    class Client:
        def publish_event(self, env, topic, *, qos, wait):
            published.append((env, topic))
            return type("R", (), {"ok": True})()

    monkeypatch.setattr(runner, "_events_cli_client", lambda: Client())
    monkeypatch.setattr(
        events_cli_adapter,
        "load_events_cli",
        lambda: type(
            "Api",
            (),
            {
                "Envelope": type("E", (), {"from_dict": staticmethod(lambda d: d)}),
                "type_to_topic": staticmethod(lambda t: t.replace(".", "/")),
            },
        ),
    )
    store = MemoryStore()
    emitter = runner.open_emitter(store, "spark")
    emitter.emit(ASK_REQUESTED, {"ask_id": "a"})
    assert [t for _, t in published] == ["human/ask/requested"]
    assert store.find(EVENTS_COLLECTION) == []  # the bus, not the store fallback


def test_run_node_wires_a_reporter_from_the_environment(node_rig, monkeypatch):
    _, captured = node_rig
    monkeypatch.setenv("CULTURE_RULES_REPORT_CHANNEL", "#rules-ops")
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    runner.run_node("spark", once=True)
    reporter = captured["kwargs"].get("reporter")
    assert isinstance(reporter, RunReporter)
    assert reporter.channel_for("any-rule") == "#rules-ops"
    assert isinstance(reporter._poster, runner.LoggingPoster)


def test_run_node_without_a_report_channel_wires_no_reporter(node_rig):
    _, captured = node_rig
    runner.run_node("spark", once=True)
    assert captured["kwargs"].get("reporter") is None


def test_the_reporter_posts_through_the_mesh_when_culture_is_on_path(monkeypatch):
    monkeypatch.setenv("CULTURE_RULES_REPORT_CHANNEL", "#ops")
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/culture")
    reporter = runner.open_reporter()
    assert isinstance(reporter._poster, runner.MeshPoster)
    calls = []
    poster = runner.MeshPoster("/usr/bin/culture", run=lambda argv, **kw: calls.append(argv))
    poster.post("#ops", "run r1 succeeded")
    assert calls == [["/usr/bin/culture", "channel", "message", "#ops", "run r1 succeeded"]]


def test_logging_poster_logs_the_report(caplog):
    with caplog.at_level("INFO", logger="culture_rules.node"):
        runner.LoggingPoster().post("#ops", "run r1 failed")
    assert "#ops" in caplog.text and "run r1 failed" in caplog.text


def test_run_node_configures_json_logging_for_its_host(node_rig, monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "configure_logging", lambda **kw: calls.append(kw))
    runner.run_node("spark", once=True)
    assert calls == [{"host": "spark"}]


def test_an_event_source_setup_failure_degrades_the_node_instead_of_crashing(monkeypatch, caplog):
    class Rejecting:
        EventsError = RuntimeError

        def open_store(self):
            return object()

        def get_subscription(self, name, registry=None):
            return None

        def add_subscription(self, name, pattern, **kw):
            raise RuntimeError("invalid subscription: pattern: must not contain '#'")

    monkeypatch.setattr(events_cli_adapter, "load_events_cli", Rejecting)
    with caplog.at_level("WARNING", logger="culture_rules.node"):
        assert runner.open_event_source("spark") is None
    assert "no event source for spark" in caplog.text


def test_the_real_events_cli_never_crashes_node_startup(monkeypatch, tmp_path):
    """With events-cli installed and no broker, the node starts degraded (no ingest)."""
    pytest.importorskip("events_cli.subs")
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv("EVENTS_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("EVENTS_BROKER_HOST", "127.0.0.1")
    monkeypatch.setenv("EVENTS_BROKER_PORT", str(port))
    assert runner.open_event_source("spark") is None
