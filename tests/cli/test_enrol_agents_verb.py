"""t31: ``actors enrol-agents`` through the CLI against ``create_app(MemoryStore())``."""

from __future__ import annotations

import pytest

pytest.importorskip("yaml")

from tests.actors.test_enrol_agents import flat, write_mesh  # noqa: E402
from tests.cli.test_nouns_api import jrun, run, snapshot, store, wire  # noqa: E402,F401


def enrol(capsys, f, *extra):
    return jrun(capsys, "actors", "enrol-agents", "--server-yaml", str(f), *extra)


def test_dry_run_sends_only_gets_and_prints_the_plan(wire, store, capsys, tmp_path):  # noqa: F811
    f = write_mesh(tmp_path, {"a": flat("a"), "b": flat("b")})
    before = snapshot(store)
    out = enrol(capsys, f)
    assert out["dry_run"] is True
    assert out["applied"] is False
    assert [(c["action"], c["id"]) for c in out["changes"]] == [
        ("create", "spark-a"),
        ("create", "spark-b"),
    ]
    assert wire.mutating() == []
    assert snapshot(store) == before
    rc, text, _ = run(capsys, "actors", "enrol-agents", "--server-yaml", str(f))
    assert rc == 0
    assert "spark-a" in text
    assert "create" in text
    assert "--apply" in text


def test_apply_creates_and_second_apply_writes_nothing(wire, store, capsys, tmp_path):  # noqa: F811
    f = write_mesh(tmp_path, {"a": flat("a")})
    out = enrol(capsys, f, "--apply")
    assert out["applied"] is True
    doc = store.get("actors", "spark-a")
    assert doc["machine"] == "spark"
    assert doc["harness"] == "claude"
    assert doc["enabled"] is True
    wire.calls.clear()
    out = enrol(capsys, f, "--apply")
    assert out["changes"] == []
    assert wire.mutating() == []


def test_removed_agent_is_disabled_not_deleted(wire, store, capsys, tmp_path):  # noqa: F811
    enrol(capsys, write_mesh(tmp_path, {"a": flat("a"), "b": flat("b")}), "--apply")
    f2 = write_mesh(tmp_path, name="v2.yaml", agents={"a": flat("a")})
    out = enrol(capsys, f2, "--apply")
    assert [(c["action"], c["id"]) for c in out["changes"]] == [("disable", "spark-b")]
    b = store.get("actors", "spark-b")
    assert b is not None
    assert b["enabled"] is False
    assert not b.get("deleted_at")
    wire.calls.clear()
    assert enrol(capsys, f2, "--apply")["changes"] == []
    assert wire.mutating() == []
    # relisting re-enables what this tool disabled
    enrol(
        capsys,
        write_mesh(tmp_path, name="v3.yaml", agents={"a": flat("a"), "b": flat("b")}),
        "--apply",
    )
    assert store.get("actors", "spark-b")["enabled"] is True


def test_missing_server_yaml_is_a_clean_error(wire, capsys, tmp_path):  # noqa: F811
    rc, _, err = run(capsys, "actors", "enrol-agents", "--server-yaml", str(tmp_path / "no.yaml"))
    assert rc == 1
    assert "server.yaml" in err
