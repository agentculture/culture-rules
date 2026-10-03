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
    kw.setdefault("pattern", "*")
    return adapter.EventsCliSource("culture-rules-h1", api=api, history=api.history, **kw)


def test_source_satisfies_the_protocol():
    api = FakeEventsCli(FakeHistory())
    assert isinstance(_source(api), EventSource)


def test_subscription_is_per_host():
    assert adapter.subscription_name("Host-1") == "culture-rules-host-1"
    with pytest.raises(ValueError):
        adapter.subscription_name("")
    src = adapter.EventsCliSource.for_host("h1", pattern="*", api=FakeEventsCli(FakeHistory()))
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
    assert batch.cursor == "3"
    assert api.drains == []  # broker untouched while history has more


def test_drain_goes_to_the_broker_when_history_is_caught_up():
    history = FakeHistory([_record(1, "a")])
    api = FakeEventsCli(history, queued=["b", "c", "d"])
    src = _source(api)
    batch = src.drain("1", max=2, timeout=0.5)
    assert [e["id"] for e in batch.envelopes] == ["evt_b", "evt_c"]
    assert batch.cursor == "3"
    assert batch.has_more is True
    assert api.drains == [("culture-rules-h1", 1, 2, 0.5)]


def test_drain_ignores_a_caller_supplied_store_option_like_ensure_does():
    history = FakeHistory([_record(1, "a")])
    seen = {}

    class Api(FakeEventsCli):
        def drain_subscription(self, name, *, since, max, timeout, **kw):
            seen.update(kw)
            return super().drain_subscription(name, since=since, max=max, timeout=timeout)

    api = Api(history, queued=["b"])
    src = _source(api, store=object())
    batch = src.drain("1", max=5, timeout=0.0)
    assert [e["id"] for e in batch.envelopes] == ["evt_b"]
    assert seen["store"] is history  # the source's own history store, not the caller's
    src.ensure()
    assert api.added == [("culture-rules-h1", "*")]


def test_drain_from_no_cursor_starts_at_zero():
    api = FakeEventsCli(FakeHistory())
    batch = _source(api).drain(None, max=5, timeout=0.0)
    assert batch.envelopes == ()
    assert batch.cursor is None


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
        adapter.EventsCliSource("s", pattern="*")


def test_real_events_cli_api_surface_when_installed():
    pytest.importorskip("events_cli.subs")
    api = adapter.load_events_cli()
    for name in ("get_subscription", "add_subscription", "drain_subscription"):
        assert callable(getattr(api, name))


# --- subscribing to every event type (events-cli rejects raw MQTT filters) ----------------------

RESERVED_MQTT = ("#", "+", "/")


class FakeSubsError(Exception):
    """Stands in for events_cli.core.errors.EventsError."""


class StrictEventsCli(FakeEventsCli):
    """A fake that enforces events-cli's pattern boundary, as ``SubscriptionRecord.new`` does."""

    EventsError = FakeSubsError

    def add_subscription(self, name, pattern, **kw):
        bad = [ch for ch in RESERVED_MQTT if ch in pattern]
        segments = pattern.split(".")
        if bad or not pattern or any(seg == "" for seg in segments):
            raise FakeSubsError(
                f"invalid subscription: pattern: must not contain the raw MQTT filter "
                f"character(s) {bad!r} (write a dotted pattern instead, e.g. 'task.*')"
            )
        return super().add_subscription(name, pattern, **kw)

    def drain_subscription(self, name, *, since, max, timeout, **kw):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise FakeSubsError(
                f"invalid drain bounds: timeout: must be a positive, finite number of "
                f"seconds, got {timeout!r}"
            )
        return super().drain_subscription(name, since=since, max=max, timeout=timeout, **kw)


def test_a_zero_timeout_drain_is_accepted_by_a_strict_events_cli():
    """The node ingests with timeout=0 ("do not wait"); events-cli needs a positive one."""
    api = StrictEventsCli(FakeHistory(), queued=["a"])
    batch = _source(api).drain(None, max=5, timeout=0.0)
    assert [e["id"] for e in batch.envelopes] == ["evt_a"]
    assert api.drains[0][3] == adapter.MIN_DRAIN_TIMEOUT


def test_no_default_pattern_uses_a_raw_mqtt_filter_character():
    for pattern in adapter.DEFAULT_PATTERNS:
        assert not any(ch in pattern for ch in RESERVED_MQTT), pattern
        assert set(pattern.split(".")) == {"*"}
    depths = sorted(len(p.split(".")) for p in adapter.DEFAULT_PATTERNS)
    assert depths == list(range(1, adapter.DEFAULT_DEPTH + 1))


def test_host_source_registers_every_depth_with_a_strict_events_cli():
    api = StrictEventsCli(FakeHistory())
    src = adapter.open_host_source("spark", api=api, history=api.history)
    assert isinstance(src, EventSource)
    assert src.name == "culture-rules-spark"
    src.ensure()
    src.ensure()
    assert [pattern for _, pattern in api.added] == list(adapter.DEFAULT_PATTERNS)
    assert len({name for name, _ in api.added}) == len(adapter.DEFAULT_PATTERNS)
    assert all(name.startswith("culture-rules-spark") for name, _ in api.added)


def test_a_rejected_pattern_surfaces_as_an_event_fabric_error():
    api = StrictEventsCli(FakeHistory())
    src = _source(api, pattern="#")
    with pytest.raises(EventFabricError, match="raw MQTT filter"):
        src.ensure()


def test_drain_failures_from_events_cli_surface_as_event_fabric_errors():
    class Api(StrictEventsCli):
        def drain_subscription(self, name, **kw):
            raise FakeSubsError("broker unreachable")

    api = Api(FakeHistory())
    with pytest.raises(EventFabricError, match="broker unreachable"):
        _source(api, pattern="*").drain(None, max=5, timeout=0.0)


def _fan_in(api, patterns=("*", "*.*")):
    return adapter.open_host_source("h1", api=api, history=api.history, patterns=patterns)


class PerSubEventsCli(StrictEventsCli):
    """Per-subscription broker queues and per-subscription history sequences."""

    def __init__(self, queued_by_sub):
        super().__init__(FakeHistory())
        self.queues = {k: list(v) for k, v in queued_by_sub.items()}
        self.history = PerSubHistory()

    def drain_subscription(self, name, *, since, max, timeout, **kw):
        assert timeout > 0, "events-cli rejects a non-positive drain timeout"
        self.drains.append((name, since, max, timeout))
        queue = self.queues.get(name, [])
        batch, self.queues[name] = queue[:max], queue[max:]
        records = [self.history.append(name, n) for n in batch]
        cursor = records[-1].seq if records else since
        return SimpleNamespace(
            records=tuple(records), cursor=cursor, has_more=bool(self.queues[name])
        )


class PerSubHistory:
    def __init__(self):
        self.logs = {}

    def append(self, sub, n):
        log = self.logs.setdefault(sub, [])
        rec = _record(len(log) + 1, n)
        log.append(rec)
        return rec

    def read(self, sub, since=0, max=100):
        log = self.logs.get(sub, [])
        page = [r for r in log if r.seq > since][:max]
        cursor = page[-1].seq if page else since
        return SimpleNamespace(
            records=tuple(page), cursor=cursor, has_more=any(r.seq > cursor for r in log)
        )


def test_fan_in_drains_every_depth_and_resumes_from_its_own_cursor():
    api = PerSubEventsCli({})
    src = _fan_in(api)
    names = [s.name for s in src.sources]
    api.queues = {names[0]: ["a"], names[1]: ["b", "c"]}
    batch = src.drain(None, max=10, timeout=0.0)
    assert sorted(e["id"] for e in batch.envelopes) == ["evt_a", "evt_b", "evt_c"]
    assert batch.has_more is False
    assert batch.cursor is not None
    api.queues[names[1]].append("d")
    again = src.drain(batch.cursor, max=10, timeout=0.0)
    assert [e["id"] for e in again.envelopes] == ["evt_d"]
    empty = src.drain(again.cursor, max=10, timeout=0.0)
    assert empty.envelopes == ()
    assert empty.cursor == again.cursor


def test_fan_in_respects_the_batch_bound_and_reports_more():
    api = PerSubEventsCli({})
    src = _fan_in(api)
    names = [s.name for s in src.sources]
    api.queues = {names[0]: ["a", "b"], names[1]: ["c", "d"]}
    seen, cursor = [], None
    for _ in range(4):
        batch = src.drain(cursor, max=3, timeout=0.0)
        assert len(batch.envelopes) <= 3
        seen += [e["id"] for e in batch.envelopes]
        cursor = batch.cursor
        if not batch.has_more:
            break
    assert sorted(seen) == ["evt_a", "evt_b", "evt_c", "evt_d"]


def test_fan_in_empty_from_the_start_keeps_a_none_cursor():
    api = PerSubEventsCli({})
    batch = _fan_in(api).drain(None, max=5, timeout=0.0)
    assert batch.envelopes == ()
    assert batch.cursor is None


def test_fan_in_rejects_a_foreign_cursor():
    api = PerSubEventsCli({})
    for bad in ("7", "not json", "[1]", '{"x": 1}'):
        with pytest.raises(EventFabricError):
            _fan_in(api).drain(bad, max=5, timeout=0.0)


# --- the real events-cli (the `events` extra) ---


@pytest.fixture
def real_events_env(monkeypatch, tmp_path):
    """events-cli state under tmp_path and a broker address nothing listens on."""
    pytest.importorskip("events_cli.subs")
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv("EVENTS_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("EVENTS_BROKER_HOST", "127.0.0.1")
    monkeypatch.setenv("EVENTS_BROKER_PORT", str(port))
    return tmp_path


def test_real_events_cli_accepts_every_default_subscription(real_events_env):
    from events_cli.subs import SubscriptionRecord

    src = adapter.open_host_source("spark-f8a9", history=FakeHistory())
    for sub in src.sources:
        record = SubscriptionRecord.new(sub.name, sub.pattern, owner="test")
        assert record.topic_filter.startswith("events/")
        assert "#" not in record.topic_filter


def test_real_events_cli_rejection_is_an_event_fabric_error(real_events_env):
    from events_cli.subs import SubscriptionRegistry

    registry = SubscriptionRegistry(real_events_env / "registry")
    src = adapter.EventsCliSource(
        "culture-rules-h1", pattern="#", history=FakeHistory(), registry=registry
    )
    with pytest.raises(EventFabricError, match="raw MQTT filter"):
        src.ensure()


def test_real_events_cli_accepts_the_nodes_zero_timeout_drain(real_events_env):
    from events_cli.subs import SubscriptionRegistry

    registry = SubscriptionRegistry(real_events_env / "registry")
    src = adapter.EventsCliSource(
        "culture-rules-h1", pattern="*", history=FakeHistory(), registry=registry
    )
    with pytest.raises(EventFabricError) as caught:  # never registered: unknown, not bad bounds
        src.drain(None, max=5, timeout=0.0)
    assert "drain bounds" not in str(caught.value)
    assert "culture-rules-h1" in str(caught.value)
