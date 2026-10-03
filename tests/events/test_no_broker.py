"""culture-rules ships no broker, MQTT server or event-history store of its own (c33, h20).

Transport and durable event history belong to events-cli; culture-rules only
subscribes through it (lazily, behind the ``events`` extra) and keeps the
events it ingests in its own StoragePort ``events`` collection.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "culture_rules"

# MQTT clients/brokers and broker-ish servers culture-rules must never import.
BROKER_MODULES = {
    "paho",
    "amqtt",
    "hbmqtt",
    "gmqtt",
    "aiomqtt",
    "asyncio_mqtt",
    "mqtt",
    "mosquitto",
    "nats",
    "kafka",
    "aiokafka",
    "confluent_kafka",
    "pika",
    "aio_pika",
    "zmq",
    "redis",
    "socketserver",
}
BROKER_DISTS = {
    "paho-mqtt",
    "amqtt",
    "hbmqtt",
    "gmqtt",
    "aiomqtt",
    "asyncio-mqtt",
    "nats-py",
    "kafka-python",
    "aiokafka",
    "confluent-kafka",
    "pika",
    "aio-pika",
    "pyzmq",
    "redis",
}
FORBIDDEN_MODULE_NAMES = {"broker", "mqtt", "mosquitto", "history", "event_history", "eventlog"}


def _imports(tree: ast.AST) -> list[tuple[str, ast.AST]]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(a.name, node) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.module, node))
    return found


def _sources():
    files = sorted(PKG.rglob("*.py"))
    assert files
    return files


def test_no_module_imports_an_mqtt_or_broker_library():
    offenders = {}
    for path in _sources():
        tops = {name.split(".")[0] for name, _ in _imports(ast.parse(path.read_text()))}
        bad = tops & BROKER_MODULES
        if bad:
            offenders[str(path.relative_to(ROOT))] = sorted(bad)
    assert not offenders, offenders


def test_no_broker_or_history_store_module_is_shipped():
    names = {p.stem for p in PKG.rglob("*.py")} | {p.name for p in PKG.rglob("*") if p.is_dir()}
    assert not names & FORBIDDEN_MODULE_NAMES


def test_the_events_package_opens_no_listening_socket():
    for path in sorted((PKG / "events").rglob("*.py")):
        tree = ast.parse(path.read_text())
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not attrs & {"bind", "listen", "serve_forever", "start_server"}, path
        assert "socket" not in {n.split(".")[0] for n, _ in _imports(tree)}, path


def test_events_cli_is_imported_lazily_and_only_by_the_adapter():
    for path in _sources():
        tree = ast.parse(path.read_text())
        hits = [node for name, node in _imports(tree) if name.split(".")[0] == "events_cli"]
        if path.name != "events_cli_adapter.py":
            assert not hits, f"{path} imports events_cli"
            continue
        top_level = {id(n) for n in tree.body}
        assert hits, "import must be lazy"
        assert all(id(n) not in top_level for n in hits), "import must be lazy"


def test_pyproject_declares_no_broker_and_events_cli_only_as_an_extra():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["dependencies"] == []
    extras = project.get("optional-dependencies", {})
    reqs = [r for group in extras.values() for r in group]

    def base(req):
        for ch in "<>=!~[; ":
            req = req.split(ch)[0]
        return req.lower().replace("_", "-")

    assert not {base(r) for r in reqs} & BROKER_DISTS
    assert [base(r) for r in extras.get("events", [])] == ["events-cli"]
    assert all("events-cli" != base(r) for name, g in extras.items() if name != "events" for r in g)


def test_importing_the_events_package_does_not_import_events_cli():
    import subprocess
    import sys

    code = (
        "import sys\n"
        "import culture_rules.events.ingest, culture_rules.events.triggers\n"
        "import culture_rules.events.emit, culture_rules.events.events_cli_adapter\n"
        "assert 'events_cli' not in sys.modules, 'eager events_cli import'\n"
        "assert not any(m.startswith('paho') for m in sys.modules)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
