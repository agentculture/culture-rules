"""Codex round 3 (pre-existing, closed alongside d25): schedule and probe events are the
engine's own.

The scheduler and the probe stage write ``schedule`` / ``probe`` events straight into the
store (kind and type ``schedule``/``probe``, sources ``culture-rules/schedule`` /
``culture-rules/probe``, ids ``schedule/<rule>/<slot>`` / ``probe/<rule>/<slot>``). A copy
from the bus or a webhook could fire a schedule or probe rule, or squat a slot's id and
suppress the genuine slot, so external ingest quarantines all of them.
"""

from __future__ import annotations

import pytest

from culture_rules.events.emit import reserved_reason
from culture_rules.events.hook_sink import sink
from culture_rules.events.ingest import EVENTS_COLLECTION, QUARANTINE_COLLECTION, EventIngest
from culture_rules.node.probe_trigger import PROBE_SOURCE, probe_event_id
from culture_rules.node.schedule import SCHEDULE_SOURCE, schedule_event_id
from culture_rules.store.memory import MemoryStore
from tests.events.fakes import FakeEventSource, envelope

FORGED = {
    "schedule_type": envelope(1, type="schedule"),
    "probe_type": envelope(2, type="probe"),
    "schedule_kind": envelope(3, kind="schedule"),
    "probe_kind": envelope(4, kind="probe"),
    "schedule_source": envelope(5, source=SCHEDULE_SOURCE),
    "probe_source": envelope(6, source=PROBE_SOURCE),
    "schedule_id": {**envelope(7), "id": schedule_event_id("r1", "2026-10-08T12:00:00+00:00")},
    "probe_id": {**envelope(8), "id": probe_event_id("r1", "2026-10-08T12:00:00+00:00")},
}


@pytest.mark.parametrize("name", sorted(FORGED))
def test_the_bus_cannot_carry_a_schedule_or_probe_event(name):
    env = FORGED[name]
    assert reserved_reason(env) is not None
    store = MemoryStore()
    (result,) = EventIngest(store, FakeEventSource([env]), host="h").ingest()
    assert result.quarantined == 1
    assert store.find(EVENTS_COLLECTION) == []
    assert len(store.find(QUARANTINE_COLLECTION)) == 1


@pytest.mark.parametrize(
    "env",
    [
        envelope(1, type="schedule.requested"),
        envelope(2, source="culture-rules/scheduler-docs"),
        {**envelope(3), "id": "scheduled_1"},
        envelope(4, kind="task"),
    ],
)
def test_neighbouring_names_still_pass(env):
    assert reserved_reason(env) is None


@pytest.mark.parametrize("kind", ["schedule", "probe"])
def test_a_webhook_cannot_inject_one_even_when_declared(kind):
    store = MemoryStore()
    actor = {"id": "a", "kind": "app", "params": {"surface": "github", "events": [kind]}}
    assert sink(store, actor, kind, {"rule_id": "r1"}, "d1", "alice") == "quarantined"
    assert store.find(EVENTS_COLLECTION) == []


def test_the_reserved_names_are_the_engines_own():
    from culture_rules.events.emit import TRIGGER_EVENT_KINDS, TRIGGER_EVENT_SOURCES
    from culture_rules.node.probe_trigger import PROBE_KIND
    from culture_rules.node.schedule import SCHEDULE_KIND

    assert TRIGGER_EVENT_KINDS == {SCHEDULE_KIND, PROBE_KIND}
    assert set(TRIGGER_EVENT_SOURCES) == {SCHEDULE_SOURCE, PROBE_SOURCE}
