"""d25, Codex round 3: a malformed type never wedges ingest.

``reserved_reason`` must never raise: a non-string ``type`` (``[]``, ``{}``) used to raise
TypeError from the settle-type check, which stopped the ingest batch before its cursor was
saved, so every later batch failed on the same envelope. Such an envelope is quarantined.
"""

from __future__ import annotations

import pytest

from culture_rules.events.emit import reserved_reason
from culture_rules.events.hook_sink import sink
from culture_rules.events.ingest import EVENTS_COLLECTION, QUARANTINE_COLLECTION, EventIngest
from culture_rules.store.memory import MemoryStore
from tests.events.fakes import FakeEventSource, envelope

MALFORMED = [[], {}, 7, ["github.pr.checks_settled"]]


@pytest.mark.parametrize("kind", MALFORMED, ids=["list", "dict", "int", "list_of_settle"])
def test_reserved_reason_never_raises_and_refuses_a_non_string_type(kind):
    assert reserved_reason(envelope(1, type=kind)) is not None


@pytest.mark.parametrize("kind", MALFORMED, ids=["list", "dict", "int", "list_of_settle"])
def test_the_bus_quarantines_it_and_moves_on(kind):
    store = MemoryStore()
    src = FakeEventSource([envelope(1, type=kind), envelope(2)])
    (result,) = EventIngest(store, src, host="h").ingest()
    assert (result.inserted, result.quarantined) == (1, 1)
    assert [e["id"] for e in store.find(EVENTS_COLLECTION)] == ["evt_2"]
    assert len(store.find(QUARANTINE_COLLECTION)) == 1
    (again,) = EventIngest(store, src, host="h").ingest()  # the cursor moved past it
    assert again.received == 0


def test_a_missing_type_is_left_to_the_existing_handling():
    env = envelope(1)
    del env["type"]
    assert reserved_reason(env) is None


@pytest.mark.parametrize("kind", [[], {}])
def test_the_webhook_sink_refuses_a_non_string_type_as_bad_input(kind):
    actor = {"id": "a", "kind": "app", "params": {"surface": "github", "events": []}}
    with pytest.raises(ValueError, match="type must be a non-empty string"):
        sink(MemoryStore(), actor, kind, {}, "d1", "alice")


# --------------------------------------------------------------------------- Codex round 4


FIELDS = ("id", "type", "kind", "source")
BAD = [None, [], {}, 7, True, ""]
BAD_IDS = ["none", "list", "dict", "int", "bool", "empty"]


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", BAD, ids=BAD_IDS)
def test_every_checked_field_that_is_present_and_not_a_non_empty_string_is_refused(
    field, value
):
    env = {**envelope(1, type="task.requested"), field: value}
    assert reserved_reason(env)  # a reason, never an exception


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", BAD, ids=BAD_IDS)
def test_the_bus_never_stores_it_and_its_cursor_advances(field, value):
    store = MemoryStore()
    bad = {**envelope(1), field: value}
    src = FakeEventSource([bad, envelope(2)])
    (result,) = EventIngest(store, src, host="h").ingest()
    # an unusable id is rejected before the reservation check; anything else is quarantined
    assert result.quarantined + result.rejected == 1
    assert [e["id"] for e in store.find(EVENTS_COLLECTION)] == ["evt_2"]
    (again,) = EventIngest(store, src, host="h").ingest()
    assert again.received == 0


@pytest.mark.parametrize("value", BAD, ids=BAD_IDS)
def test_the_webhook_sink_rejects_a_bad_type_with_its_documented_error(value):
    actor = {"id": "a", "kind": "app", "params": {"surface": "github", "events": []}}
    with pytest.raises(ValueError, match="type must be a non-empty string"):
        sink(MemoryStore(), actor, value, {}, "d1", "alice")


def test_an_absent_field_keeps_todays_handling():
    for field in ("type", "kind", "source"):
        env = envelope(1)
        env.pop(field, None)
        assert reserved_reason(env) is None, field
