"""Emitted envelopes carry correlationId / causationId / runId lineage."""

from __future__ import annotations

import re

import pytest

from culture_rules.events.emit import Emitter, EventSink, derive_envelope, new_event_id
from tests.events.fakes import envelope


class ListSink:
    def __init__(self):
        self.published = []

    def publish(self, env):
        self.published.append(env)


def test_new_event_id_is_evt_prefixed_ulid_and_unique():
    ids = {new_event_id() for _ in range(500)}
    assert len(ids) == 500
    assert all(re.fullmatch(r"evt_[0-9A-HJKMNP-TV-Z]{26}", i) for i in ids)


def test_derived_envelope_points_at_its_cause():
    cause = envelope(1)
    out = derive_envelope(cause, type="rule.fired", source="culture-rules://engine")
    assert out["causationId"] == "evt_1"
    assert out["correlationId"] == "evt_1"  # a root cause starts the correlation
    assert "runId" not in out
    assert out["id"] != "evt_1"
    assert out["id"].startswith("evt_")
    assert out["schemaVersion"] == "1"
    assert out["data"] == {}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", out["time"])


def test_correlation_and_run_are_inherited_through_a_chain():
    root = envelope(1, correlationId="corr_root", runId="run_1")
    child = derive_envelope(root, type="a.b", source="x://y")
    grandchild = derive_envelope(child, type="a.c", source="x://y", data={"k": 1})
    assert child["correlationId"] == grandchild["correlationId"] == "corr_root"
    assert child["runId"] == grandchild["runId"] == "run_1"
    assert grandchild["causationId"] == child["id"]
    assert grandchild["data"] == {"k": 1}


def test_explicit_run_id_overrides_the_inherited_one():
    out = derive_envelope(envelope(1, runId="run_old"), type="a.b", source="x://y", run_id="r2")
    assert out["runId"] == "r2"


def test_derive_accepts_a_stored_event_document():
    doc = {"id": "evt_1", "envelope": envelope(1, correlationId="c1"), "host": "h"}
    out = derive_envelope(doc, type="a.b", source="x://y")
    assert (out["causationId"], out["correlationId"]) == ("evt_1", "c1")


def test_derive_rejects_a_cause_without_an_id():
    with pytest.raises(ValueError):
        derive_envelope({"type": "x"}, type="a.b", source="x://y")


def test_emitter_publishes_root_and_caused_events():
    sink = ListSink()
    assert isinstance(sink, EventSink)
    emitter = Emitter(sink, source="culture-rules://engine")
    root = emitter.emit("run.started", {"rule": "r"}, run_id="run_7")
    assert root["correlationId"] == root["id"]
    assert root["runId"] == "run_7"
    assert "causationId" not in root
    child = emitter.emit("run.finished", cause=root)
    assert child["causationId"] == root["id"]
    assert child["correlationId"] == root["id"]
    assert child["runId"] == "run_7"
    assert sink.published == [root, child]


def test_derived_envelopes_validate_against_events_cli_when_installed():
    events_cli_envelope = pytest.importorskip("events_cli.core.envelope")
    from culture_rules.events.events_cli_adapter import to_bus

    out = derive_envelope(envelope(1, runId="run_1"), type="rule.fired", source="cr://engine")
    assert "hops" in out  # events-cli has no hops field: the sink moves it into data
    parsed = events_cli_envelope.Envelope.from_dict(to_bus(out))
    assert (parsed.causation_id, parsed.run_id) == ("evt_1", "run_1")
