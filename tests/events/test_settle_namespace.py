"""d25, Codex round 2: the checks-settle events are the engine's own.

``github.pr.checks_settled`` and ``github.pr.checks_failed_late`` (and their deterministic id
prefixes ``settled_`` and ``late_``) are written only by the settler, from its internal
source. A copy from the bus or a webhook could fire the fixer or the GitGuardian report, or
squat the deterministic id and suppress the genuine event, so external ingest quarantines
them.
"""

from __future__ import annotations

import pytest

from culture_rules.events.emit import INTERNAL_SOURCE_PREFIX, reserved_reason
from culture_rules.events.hook_sink import sink
from culture_rules.events.ingest import EVENTS_COLLECTION, QUARANTINE_COLLECTION, EventIngest
from culture_rules.node.checks_settle import (
    LATE_TYPE,
    SETTLED_TYPE,
    SOURCE,
    late_event_id,
    settled_event_id,
)
from culture_rules.store.memory import MemoryStore
from tests.events.fakes import FakeEventSource, envelope

FORGED = [
    envelope(1, type=LATE_TYPE),
    envelope(2, type=SETTLED_TYPE),
    {**envelope(3), "id": late_event_id("o/r", "a" * 40, "gitguardian")},
    {**envelope(4), "id": settled_event_id("o/r", "a" * 40)},
]


@pytest.mark.parametrize("env", FORGED, ids=["late_type", "settled_type", "late_id", "settled_id"])
def test_the_bus_cannot_carry_a_settle_event(env):
    assert reserved_reason(env) is not None
    store = MemoryStore()
    (result,) = EventIngest(store, FakeEventSource([env]), host="h").ingest()
    assert result.quarantined == 1
    assert store.find(EVENTS_COLLECTION) == []
    assert len(store.find(QUARANTINE_COLLECTION)) == 1


def test_an_ordinary_event_still_passes():
    assert reserved_reason(envelope(5, type="github.checks.suite_completed")) is None
    assert reserved_reason({**envelope(6), "id": "lately_1"}) is None


@pytest.mark.parametrize("kind", [LATE_TYPE, SETTLED_TYPE])
def test_a_webhook_cannot_inject_a_settle_event_even_when_declared(kind):
    store = MemoryStore()
    actor = {
        "id": "gh-app",
        "kind": "app",
        "enabled": True,
        "params": {"surface": "github", "events": [kind]},
    }
    assert sink(store, actor, kind, {"n": 1}, "d1", "alice") == "quarantined"
    assert store.find(EVENTS_COLLECTION) == []


def test_the_settler_writes_from_the_internal_source():
    assert SOURCE.startswith(INTERNAL_SOURCE_PREFIX)
