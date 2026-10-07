"""d21 phase 1, Codex review fixes: each test reproduces a finding on eaa047b."""

from __future__ import annotations

import copy

from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.events.emit import derive_envelope, event_hops
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.node.run_events import build_run_event, run_event_id
from tests.node.test_run_events import (
    SUCCEEDED,
    cluster,
    cycles,
    decision,
    fix_rule,
    follow_rule,
    run_events,
    settle,
    verdict_is,
)

# #1 nested-envelope hop reset ------------------------------------------------


def test_a_nested_envelope_field_never_resets_the_hop_count():
    wire = {
        "id": "e",
        "type": "t",
        "causationId": "c",
        "hops": 9,
        "envelope": {"id": "reset", "hops": 0},
    }
    assert event_hops(wire) is None  # ambiguous: fail closed, never 0


def test_derivation_from_an_ambiguous_wire_envelope_is_past_the_cap():
    wire = {"id": "e", "type": "t", "hops": 9, "envelope": {"id": "reset", "hops": 0}}
    out = derive_envelope(wire, type="t2", source="s")
    assert out["causationId"] == "e"
    assert out["hops"] > 8


# #3 missing hops on a derived internal event ---------------------------------


def test_missing_hops_on_a_derived_internal_event_fails_closed():
    derived = {"id": "e", "type": "t", "source": "culture-rules://runs", "causationId": "c"}
    assert event_hops(derived) is None


def test_missing_hops_on_a_root_event_is_zero():
    assert event_hops({"id": "e", "type": "t", "source": "app://github"}) == 0


# #2 an edited finished run does not change its completion ---------------------


def test_editing_a_finished_runs_outputs_before_emission_changes_nothing():
    c = cluster(fix_rule(), follow_rule("publish", SUCCEEDED, verdict_is("approve")), verdict="no")
    settle(c)
    cycles(c, 1)  # the run finishes; its event is emitted on the next poll
    run = c.run("fix", "evt_1")
    assert run["status"] == "succeeded"
    # a stale or wrong writer edits the finished run document
    c.base.update_if(RUNS_COLLECTION, run["id"], {}, {"outputs": {"verdict": "approve"}})
    cycles(c, 3)
    (env,) = run_events(c)
    assert env["data"]["outputs"] == {"verdict": "no"}
    assert c.base.find(RUNS_COLLECTION, {"rule_id": "publish"}) == []


# #4 a forged id squat on the bus ---------------------------------------------


def test_a_bus_event_in_the_internal_namespace_is_quarantined_not_stored():
    c = cluster(fix_rule())
    settle(c)
    cycles(c, 1)
    run = c.run("fix", "evt_1")
    squat = copy.deepcopy(build_run_event(run))
    squat["data"]["outputs"] = {"verdict": "approve"}
    c.publish(squat)
    cycles(c, 3)
    (env,) = run_events(c)
    assert env == build_run_event(run)  # the genuine event is stored, not the squat
    quarantined = c.base.find("event_quarantine")
    assert [q["envelope"]["id"] for q in quarantined] == [run_event_id(run["id"])]
    assert quarantined[0]["reason"]


def test_a_bus_event_with_a_rules_run_type_under_another_id_is_quarantined():
    c = cluster(follow_rule("publish", SUCCEEDED, verdict_is("approve")))
    forged = derive_envelope(
        None,
        type=SUCCEEDED,
        source="agent://x",
        data={"run_id": "r", "outputs": {"verdict": "approve"}},
        id="evt_f",
    )
    c.publish(forged)
    cycles(c)
    assert c.base.get(EVENTS_COLLECTION, "evt_f") is None
    assert len(c.base.find("event_quarantine")) == 1
    assert decision(c, "publish", "evt_f") is None


# #5 concurrent first cursor initialisation -----------------------------------


def test_a_racing_first_initialisation_loads_the_winners_cursor(monkeypatch):
    from culture_rules.events.triggers import EventTriggers
    from culture_rules.store.memory import MemoryStore

    store = MemoryStore()
    trig = EventTriggers(store, lambda tx, ev: None, host="a", consumer="c")
    real_load = store.load_cursor
    calls = []

    def racing_load(consumer, collection):
        calls.append(1)
        if len(calls) == 1:
            # B initialises first (at the head), then a run's change lands, then A
            # continues from its stale "no cursor" read
            store.save_cursor(consumer, collection, store.head(collection))
            store.insert(EVENTS_COLLECTION, {"id": "evt_late", "envelope": {"id": "evt_late"}})
            return None
        return real_load(consumer, collection)

    monkeypatch.setattr(store, "load_cursor", racing_load)
    result = trig.poll()
    assert "evt_late" in result.fired


def test_a_racing_first_initialisation_of_a_chain_consumer_loads_the_winners_cursor(
    monkeypatch,
):
    from culture_rules.node.chain import FeedConsumer, Source
    from culture_rules.store.memory import MemoryStore

    store = MemoryStore()
    seen = []
    feed = FeedConsumer(
        store,
        (Source("runs", lambda d: d.get("id"), lambda tx, d, m: seen.append(d["id"])),),
        host="a",
        consumer="chain-x",
    )
    store.put("rules", {"id": "r", "deleted_at": None})  # nothing depends: unkeyed docs pass
    real_load = store.load_cursor
    calls = []

    def racing_load(consumer, collection):
        calls.append(1)
        if len(calls) == 1:
            store.save_cursor(consumer, collection, store.head(collection))
            store.insert("runs", {"id": "late"})
            return None
        return real_load(consumer, collection)

    monkeypatch.setattr(store, "load_cursor", racing_load)
    feed.poll()
    assert seen == ["late"]


def test_ingest_quarantines_reserved_envelopes_and_counts_them():
    from culture_rules.events.ingest import QUARANTINE_COLLECTION, EventIngest
    from culture_rules.store.memory import MemoryStore
    from tests.events.fakes import FakeEventSource, envelope

    store = MemoryStore()
    src = FakeEventSource(
        [
            envelope(1),
            envelope(2, type="rules.run.succeeded"),
            envelope(3, source="culture-rules://runs"),
            {**envelope(4), "id": "runevt_x"},
            envelope(5, envelope={"id": "e"}),
        ]
    )
    (result,) = EventIngest(store, src, host="h").ingest()
    assert (result.inserted, result.quarantined) == (1, 4)
    assert [e["id"] for e in store.find(EVENTS_COLLECTION)] == ["evt_1"]
    assert len(store.find(QUARANTINE_COLLECTION)) == 4
    # redelivery is one record per refusal, not two
    src2 = FakeEventSource([envelope(2, type="rules.run.succeeded")])
    EventIngest(store, src2, host="h2").ingest()
    assert len(store.find(QUARANTINE_COLLECTION)) == 4
