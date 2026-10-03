"""Structured logs, run-id context and cross-node step trails (t22)."""

from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime

from culture_rules.engine.audit import AuditLog
from culture_rules.events.emit import Emitter
from culture_rules.ops.logs import (
    JsonFormatter,
    current_context,
    emit_for_run,
    log_context,
    stamp_envelope,
    step_trail,
)
from culture_rules.store.memory import MemoryStore


def _logger(host="node-a"):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(host=host))
    log = logging.getLogger(f"t22.{id(stream)}")
    log.handlers = [handler]
    log.propagate = False
    log.setLevel(logging.INFO)
    return log, stream


def test_json_log_carries_run_step_host():
    log, stream = _logger()
    with log_context(run_id="run-1", step_id="s1"):
        log.info("dispatching")
    rec = json.loads(stream.getvalue())
    assert rec["run_id"] == "run-1"
    assert rec["step_id"] == "s1"
    assert rec["host"] == "node-a"
    assert rec["message"] == "dispatching"
    assert rec["level"] == "INFO"


def test_context_nests_and_restores():
    log, stream = _logger()
    with log_context(run_id="r"):
        with log_context(step_id="s"):
            assert current_context() == {"run_id": "r", "step_id": "s", "host": None}
        assert current_context()["step_id"] is None
    assert current_context()["run_id"] is None
    log.info("outside")
    rec = json.loads(stream.getvalue())
    assert rec["run_id"] is None and rec["step_id"] is None


def test_context_host_overrides_formatter_host():
    log, stream = _logger()
    with log_context(host="other"):
        log.info("x")
    assert json.loads(stream.getvalue())["host"] == "other"


def test_exception_is_serialised():
    log, stream = _logger()
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("failed")
    rec = json.loads(stream.getvalue())
    assert "ValueError: boom" in rec["exc"]


def _entry(store, host, run_id, identity="u"):
    AuditLog(host=host).write(
        store,
        identity=identity,
        verb="run.cancel",
        collection="runs",
        target_id=run_id,
        before={"status": "running"},
        after={"status": "cancelled"},
    )


def test_step_trail_across_two_nodes():
    a = MemoryStore()
    b = a.peer("1.0")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    a.insert(
        "runs",
        {
            "id": "run-1",
            "status": "running",
            "history": [
                {
                    "rev": 1,
                    "at": "2026-01-01T00:00:01Z",
                    "host": "node-a",
                    "event": "start",
                    "step": None,
                },
                {
                    "rev": 2,
                    "at": "2026-01-01T00:00:02Z",
                    "host": "node-a",
                    "event": "dispatch",
                    "step": "s1",
                },
            ],
        },
    )
    # node b advances the same run
    doc = b.get("runs", "run-1")
    hist = doc["history"] + [
        {
            "rev": 3,
            "at": "2026-01-01T00:00:03Z",
            "host": "node-b",
            "event": "complete",
            "step": "s2",
        }
    ]
    b.put("runs", {**{k: v for k, v in doc.items() if k != "history"}, "history": hist})
    a.insert(
        "runs",
        {
            "id": "other",
            "history": [
                {
                    "rev": 1,
                    "at": "2026-01-01T00:00:00Z",
                    "host": "x",
                    "event": "start",
                    "step": None,
                }
            ],
        },
    )
    del now
    for store in (a, b):
        trail = step_trail(store, "run-1")
        assert [t["event"] for t in trail] == ["start", "dispatch", "complete"]
        assert {t["host"] for t in trail} == {"node-a", "node-b"}
        assert [t["step"] for t in trail] == [None, "s1", "s2"]


def test_step_trail_includes_audit_and_unknown_run_is_empty():
    store = MemoryStore()
    store.insert("runs", {"id": "r", "history": []})
    _entry(store, "node-b", "r")
    trail = step_trail(store, "r")
    assert len(trail) == 1
    assert trail[0]["source"] == "audit" and trail[0]["host"] == "node-b"
    assert trail[0]["event"] == "run.cancel"
    assert step_trail(store, "missing") == []


class _Sink:
    def __init__(self):
        self.sent = []

    def publish(self, envelope):
        self.sent.append(envelope)


def test_stamp_envelope_adds_run_and_correlation():
    env = {"id": "evt_1", "type": "t"}
    out = stamp_envelope(env, run_id="run-1", correlation_id="corr-1")
    assert out["runId"] == "run-1" and out["correlationId"] == "corr-1"
    assert "runId" not in env  # input untouched


def test_stamp_envelope_keeps_existing_correlation_and_uses_context():
    env = {"id": "evt_1", "correlationId": "keep"}
    with log_context(run_id="ctx-run"):
        out = stamp_envelope(env)
    assert out["runId"] == "ctx-run" and out["correlationId"] == "keep"


def test_emit_for_run_publishes_stamped_envelope():
    sink = _Sink()
    emitter = Emitter(sink, source="culture-rules/test")
    env = emit_for_run(emitter, "rule.fired", {"a": 1}, run_id="run-9", correlation_id="c-9")
    assert sink.sent == [env]
    assert env["runId"] == "run-9" and env["correlationId"] == "c-9"
    assert env["data"] == {"a": 1}


def test_emit_for_run_defaults_correlation_from_cause_then_run():
    sink = _Sink()
    emitter = Emitter(sink, source="s")
    cause = {"id": "evt_c", "correlationId": "chain"}
    env = emit_for_run(emitter, "x", cause=cause, run_id="r")
    assert env["correlationId"] == "chain" and env["causationId"] == "evt_c"
    env2 = emit_for_run(emitter, "x", run_id="r2")
    assert env2["runId"] == "r2" and env2["correlationId"]
