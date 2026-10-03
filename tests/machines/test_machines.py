"""t8: enrolment, heartbeats, offline detection and the platform probe."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.machines.enrol import enrol, enrolled_machines, unenrol
from culture_rules.machines.heartbeat import (
    HEARTBEAT_COLLECTION,
    HEARTBEAT_INTERVAL_S,
    OFFLINE_AFTER_S,
    HeartbeatPublisher,
    online_machines,
    placeable_machines,
)
from culture_rules.machines.probe import probe_platform, probe_usb
from culture_rules.model.machine import Machine
from culture_rules.store.memory import MemoryStore


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _machine(name="spark", **kw):
    kw.setdefault("address", "spark.tail1234.ts.net")
    kw.setdefault("platform", "linux-aarch64")
    kw.setdefault("capabilities", ("gpu",))
    kw.setdefault("roles", ("engine_node",))
    return Machine(name=name, **kw)


# --- criterion 1: enrol / unenrol ---------------------------------------


def test_enrol_is_dry_run_by_default():
    store = MemoryStore()
    result = enrol(store, _machine())
    assert result.applied is False
    assert result.action == "create"
    assert store.get("machines", "spark") is None


def test_enrol_apply_stores_machine_record():
    store = MemoryStore()
    result = enrol(store, _machine(), apply=True)
    assert result.applied is True
    stored = enrolled_machines(store)
    assert stored == [_machine()]
    assert stored[0].address == "spark.tail1234.ts.net"
    assert stored[0].roles == ("engine_node",)


def test_enrol_again_updates_and_dry_run_reports_update():
    store = MemoryStore()
    enrol(store, _machine(), apply=True)
    changed = _machine(capabilities=("gpu", "usb"))
    assert enrol(store, changed).action == "update"
    assert enrolled_machines(store)[0].capabilities == ("gpu",)
    enrol(store, changed, apply=True)
    assert enrolled_machines(store)[0].capabilities == ("gpu", "usb")


def test_enrol_unchanged_is_noop():
    store = MemoryStore()
    enrol(store, _machine(), apply=True)
    assert enrol(store, _machine(), apply=True).action == "unchanged"


def test_enrol_rejects_invalid_machine():
    store, nameless = MemoryStore(), _machine(name="")
    with pytest.raises(ValueError):
        enrol(store, nameless, apply=True)


def test_unenrol_dry_run_then_apply():
    store = MemoryStore()
    enrol(store, _machine(), apply=True)
    assert unenrol(store, "spark").applied is False
    assert len(enrolled_machines(store)) == 1
    result = unenrol(store, "spark", apply=True)
    assert result.applied
    assert result.action == "delete"
    assert enrolled_machines(store) == []
    assert unenrol(store, "spark", apply=True).action == "absent"


def test_enrolled_machines_tolerates_unknown_fields():
    store = MemoryStore()
    store.put("machines", {**_machine().to_dict(), "id": "spark", "future_field": 1})
    assert enrolled_machines(store)[0].name == "spark"


# --- criterion 2: heartbeats --------------------------------------------


def _publisher(store, clock, name="spark", **kw):
    kw.setdefault("tools", {"nvidia-smi": True, "tegrastats": False})
    kw.setdefault("load_reader", lambda: {"cpu": 0.5, "mem": 0.25})
    return HeartbeatPublisher(store, name, clock=clock, engine_version="9.9.9", **kw)


def test_constants():
    assert HEARTBEAT_INTERVAL_S == 10
    assert OFFLINE_AFTER_S == 30


def test_heartbeat_document_shape():
    store, clock = MemoryStore(), FakeClock()
    _publisher(store, clock).beat()
    doc = store.get(HEARTBEAT_COLLECTION, "spark")
    assert doc["machine"] == "spark"
    assert doc["ts"] == "2026-01-01T00:00:00Z"
    assert doc["load"] == {"cpu": 0.5, "mem": 0.25}
    assert doc["tools"] == {"nvidia-smi": True, "tegrastats": False}
    assert doc["engine_version"] == "9.9.9"


def test_heartbeat_includes_optional_gpu_load():
    store, clock = MemoryStore(), FakeClock()
    pub = _publisher(store, clock, load_reader=lambda: {"cpu": 1.0, "mem": 0.5, "gpu": 0.75})
    pub.beat()
    assert store.get(HEARTBEAT_COLLECTION, "spark")["load"]["gpu"] == 0.75


def test_heartbeat_replaces_previous_document():
    store, clock = MemoryStore(), FakeClock()
    pub = _publisher(store, clock)
    pub.beat()
    clock.advance(10)
    pub.beat()
    assert len(store.find(HEARTBEAT_COLLECTION)) == 1
    assert store.get(HEARTBEAT_COLLECTION, "spark")["ts"] == "2026-01-01T00:00:10Z"


def test_publisher_run_beats_every_interval_with_injected_sleep():
    store, clock = MemoryStore(), FakeClock()
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.advance(seconds)

    pub = _publisher(store, clock)
    pub.run(sleep=sleep, max_beats=3)
    assert sleeps == [10, 10]
    assert store.get(HEARTBEAT_COLLECTION, "spark")["ts"] == "2026-01-01T00:00:20Z"


def test_three_missed_heartbeats_mark_offline_and_placement_skips():
    store, clock = MemoryStore(), FakeClock()
    for name in ("spark", "thor"):
        enrol(store, _machine(name), apply=True)
    pubs = {n: _publisher(store, clock, n) for n in ("spark", "thor")}
    for p in pubs.values():
        p.beat()
    assert online_machines(store, clock()) == {"spark", "thor"}
    # thor keeps beating; spark goes silent.
    for _ in range(2):
        clock.advance(10)
        pubs["thor"].beat()
        assert "spark" in online_machines(store, clock())  # 10s / 20s: still online
    clock.advance(10)  # 30 s without a beat from spark
    pubs["thor"].beat()
    assert online_machines(store, clock()) == {"thor"}
    assert [m.name for m in placeable_machines(store, clock())] == ["thor"]
    pubs["spark"].beat()  # recovers
    assert online_machines(store, clock()) == {"spark", "thor"}


def test_placeable_requires_enrolled_enabled_engine_node_and_capabilities():
    store, clock = MemoryStore(), FakeClock()
    enrol(store, _machine("a"), apply=True)
    enrol(store, _machine("b", capabilities=()), apply=True)
    enrol(store, _machine("c", enabled=False), apply=True)
    enrol(store, _machine("d", roles=("runner",)), apply=True)
    for n in "abcd":
        _publisher(store, clock, n).beat()
    _publisher(store, clock, "ghost").beat()  # heartbeat but never enrolled
    assert [m.name for m in placeable_machines(store, clock())] == ["a", "b"]
    assert [m.name for m in placeable_machines(store, clock(), requirement=("gpu",))] == ["a"]


def test_machine_without_heartbeat_is_not_placeable():
    store, clock = MemoryStore(), FakeClock()
    enrol(store, _machine(), apply=True)
    assert placeable_machines(store, clock()) == []


# --- criterion 3: platform probe ----------------------------------------


def test_probe_records_present_and_absent_tools():
    found = {"nvidia-smi": "/usr/bin/nvidia-smi"}
    result = probe_platform(which=found.get)
    assert result.tools == {"nvidia-smi": True, "tegrastats": False}


def test_probe_never_runs_shell_or_fails_when_tools_missing():
    def boom(*a, **k):
        raise FileNotFoundError("no such tool")

    result = probe_platform(which=lambda _t: None, run=boom)
    assert result.tools == {"nvidia-smi": False, "tegrastats": False}
    assert result.gpu_load is None


def test_probe_gpu_query_uses_argv_list_and_timeout():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="42\n", stderr="")

    result = probe_platform(which=lambda t: "/x/" + t if t == "nvidia-smi" else None, run=run)
    assert result.gpu_load == pytest.approx(0.42)
    argv, kwargs = calls[0]
    assert isinstance(argv, list)
    assert argv[0] == "nvidia-smi"
    assert kwargs.get("timeout")
    assert kwargs["timeout"] <= 10
    assert not kwargs.get("shell")


@pytest.mark.parametrize(
    "exc", [subprocess.TimeoutExpired("nvidia-smi", 5), OSError("x"), ValueError("x")]
)
def test_probe_gpu_failure_is_recorded_not_fatal(exc):
    def run(argv, **kwargs):
        raise exc

    result = probe_platform(which=lambda t: "/x" if t == "nvidia-smi" else None, run=run)
    assert result.tools["nvidia-smi"] is True
    assert result.gpu_load is None


def test_probe_gpu_garbage_output_is_not_fatal():
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="not a number", stderr="")

    assert probe_platform(which=lambda t: "/x", run=run).gpu_load is None


def test_probe_does_not_touch_usb_unless_requested():
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    probe_platform(which=lambda t: "/x", run=run)
    assert all(a[0] != "lsusb" for a in calls)


def test_usb_probe_runs_only_on_request_and_matches_ids():
    calls = []
    out = "Bus 001 Device 004: ID 1a86:7523 QinHeng CH340\nBus 001 Device 001: ID 1d6b:0002 hub\n"

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    assert probe_usb([], which=lambda t: "/x", run=run) == {}
    assert calls == []
    got = probe_usb(["1a86:7523", "dead:beef"], which=lambda t: "/x", run=run)
    assert got == {"1a86:7523": True, "dead:beef": False}
    assert calls[0][0] == ["lsusb"]
    assert calls[0][1]["timeout"] <= 10


def test_usb_probe_missing_lsusb_is_absent_not_fatal():
    got = probe_usb(["1a86:7523"], which=lambda t: None)
    assert got == {"1a86:7523": False}


def test_usb_probe_failure_is_not_fatal():
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired("lsusb", 5)

    assert probe_usb(["1a86:7523"], which=lambda t: "/x", run=run) == {"1a86:7523": False}


def test_real_probe_runs_here_without_raising():
    result = probe_platform()
    assert set(result.tools) == {"nvidia-smi", "tegrastats"}
