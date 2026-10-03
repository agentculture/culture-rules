"""The events-cli adapters, driven through injected fakes (events_cli not required)."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from culture_rules.events import events_cli_adapter as adapter
from culture_rules.events.source import EventFabricError, EventSource


class FakeEnvelope:
    def __init__(self, wire):
        self.wire = wire

    def to_dict(self):
        return dict(self.wire)

    @classmethod
    def from_dict(cls, wire):
        return cls(wire)


def _record(seq, n):
    return SimpleNamespace(seq=seq, envelope=FakeEnvelope({"id": f"evt_{n}"}))


class FakeHistory:
    def __init__(self, records=()):
        self.records = list(records)
        self.reads = []

    def read(self, sub, since=0, max=100):
        self.reads.append((sub, since, max))
        page = [r for r in self.records if r.seq > since][:max]
        cursor = page[-1].seq if page else since
        more = any(r.seq > cursor for r in self.records)
        return SimpleNamespace(records=tuple(page), cursor=cursor, has_more=more)


class FakeEventsCli:
    """Stands in for the events_cli API surface the adapter uses."""

    def __init__(self, history, queued=()):
        self.history = history
        self.queued = list(queued)
        self.registered = {}
        self.added = []
        self.drains = []
        self.published = []

    def open_store(self):
        return self.history

    # subs
    def get_subscription(self, name, registry=None):
        return self.registered.get(name)

    def add_subscription(self, name, pattern, **kw):
        self.added.append((name, pattern))
        self.registered[name] = SimpleNamespace(name=name, pattern=pattern)
        return self.registered[name]

    def drain_subscription(self, name, *, since, max, timeout, **kw):
        self.drains.append((name, since, max, timeout))
        batch, self.queued = self.queued[:max], self.queued[max:]
        start = len(self.history.records)  # the store's append sequence
        records = []
        for i, n in enumerate(batch, start=1):
            rec = _record(start + i, n)
            self.history.records.append(rec)
            records.append(rec)
        cursor = records[-1].seq if records else since
        return SimpleNamespace(records=tuple(records), cursor=cursor, has_more=bool(self.queued))


def _source(api, **kw):
    return adapter.EventsCliSource("culture-rules-h1", api=api, history=api.history, **kw)


def test_source_satisfies_the_protocol():
    api = FakeEventsCli(FakeHistory())
    assert isinstance(_source(api), EventSource)


def test_subscription_is_per_host():
    assert adapter.subscription_name("Host-1") == "culture-rules-host-1"
    with pytest.raises(ValueError):
        adapter.subscription_name("")
    src = adapter.EventsCliSource.for_host("h1", api=FakeEventsCli(FakeHistory()))
    assert src.name == "culture-rules-h1"


def test_ensure_registers_the_durable_subscription_once():
    api = FakeEventsCli(FakeHistory())
    src = _source(api, pattern="task.*")
    src.ensure()
    src.ensure()
    assert api.added == [("culture-rules-h1", "task.*")]


def test_drain_first_replays_persisted_history_past_the_cursor():
    history = FakeHistory([_record(1, "a"), _record(2, "b"), _record(3, "c")])
    api = FakeEventsCli(history, queued=["d"])
    batch = _source(api).drain("1", max=10, timeout=0.5)
    assert [e["id"] for e in batch.envelopes] == ["evt_b", "evt_c"]
    assert batch.cursor == "3" and api.drains == []  # broker untouched while history has more


def test_drain_goes_to_the_broker_when_history_is_caught_up():
    history = FakeHistory([_record(1, "a")])
    api = FakeEventsCli(history, queued=["b", "c", "d"])
    src = _source(api)
    batch = src.drain("1", max=2, timeout=0.5)
    assert [e["id"] for e in batch.envelopes] == ["evt_b", "evt_c"]
    assert batch.cursor == "3" and batch.has_more is True
    assert api.drains == [("culture-rules-h1", 1, 2, 0.5)]


def test_drain_from_no_cursor_starts_at_zero():
    api = FakeEventsCli(FakeHistory())
    batch = _source(api).drain(None, max=5, timeout=0.0)
    assert batch.envelopes == () and batch.cursor is None


def test_drain_rejects_a_foreign_cursor():
    with pytest.raises(EventFabricError):
        _source(FakeEventsCli(FakeHistory())).drain("not-a-number", max=5, timeout=0.0)


def test_sink_publishes_via_the_client_with_qos1_on_the_type_topic():
    calls = []

    class Client:
        def publish_event(self, env, topic, *, qos, wait):
            calls.append((env.wire, topic, qos, wait))
            return SimpleNamespace(ok=True)

    sink = adapter.EventsCliSink(
        Client(), envelope_cls=FakeEnvelope, topic_for=lambda t: "events/" + t.replace(".", "/")
    )
    sink.publish({"id": "evt_1", "type": "rule.fired"})
    assert calls == [({"id": "evt_1", "type": "rule.fired"}, "events/rule/fired", 1, 5.0)]


def test_sink_raises_when_the_publish_is_not_confirmed():
    class Client:
        def publish_event(self, env, topic, *, qos, wait):
            return SimpleNamespace(ok=False, reason="broker down")

    sink = adapter.EventsCliSink(Client(), envelope_cls=FakeEnvelope, topic_for=str)
    with pytest.raises(EventFabricError, match="broker down"):
        sink.publish({"id": "evt_1", "type": "a.b"})


def test_missing_events_cli_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "events_cli", None)
    monkeypatch.setitem(sys.modules, "events_cli.subs", None)
    with pytest.raises(EventFabricError, match=r"culture-rules\[events\]"):
        adapter.load_events_cli()
    with pytest.raises(EventFabricError, match=r"culture-rules\[events\]"):
        adapter.EventsCliSource("s")


def test_real_events_cli_api_surface_when_installed():
    pytest.importorskip("events_cli.subs")
    api = adapter.load_events_cli()
    for name in ("get_subscription", "add_subscription", "drain_subscription"):
        assert callable(getattr(api, name))
