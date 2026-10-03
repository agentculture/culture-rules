"""Replay (t20): recorded envelopes through matching, would-fire reasons, zero actions."""

from __future__ import annotations

import copy

import pytest

from culture_rules.engine.matching import CONDITION_FALSE, DISABLED, FIRE, GROUP_LOST
from culture_rules.engine.replay import ReplayError, replay
from culture_rules.engine.runs import RUN_COLLECTIONS
from culture_rules.events.ingest import EVENTS_COLLECTION, event_document
from culture_rules.events.triggers import FIRES_COLLECTION
from culture_rules.model.action import Action
from culture_rules.model.rule import Rule, Trigger
from culture_rules.store.memory import MemoryStore
from culture_rules.store.port import CURSOR_COLLECTION

TRUE = {"op": "compare", "cmp": "==", "left": {"field": "data.base"}, "right": {"literal": "main"}}


def env(i: int, base: str = "main", type_: str = "github.pr.merged") -> dict:
    return {"id": f"e{i}", "kind": "event", "type": type_, "data": {"base": base, "number": i}}


def rule(rid: str, **kw) -> Rule:
    base = dict(
        id=rid,
        name=rid,
        trigger=Trigger(kind="event", params={"type": "github.pr.merged"}),
        action=Action(kind="mesh.message"),
    )
    base.update(kw)
    return Rule(**base)


def test_replays_n_envelopes_and_reports_would_fire_with_reasons():
    envs = [env(1), env(2, base="dev"), env(3), env(4, type_="other")]
    report = replay(envs, [rule("a", condition=TRUE)])
    assert report.events == 4
    assert [(w.event_id, w.rule_id) for w in report.would_fire] == [("e1", "a"), ("e3", "a")]
    assert all(w.reason == FIRE and w.message == "matched" for w in report.would_fire)
    skipped = [(s.event_id, s.reason) for s in report.skipped]
    assert skipped == [("e2", CONDITION_FALSE)]
    assert report.unmatched == ("e4",)
    assert report.to_dict()["would_fire_count"] == 2


def test_skip_reasons_are_reported():
    rules = [rule("a", priority=2, exclusive_group="g"), rule("b", exclusive_group="g")]
    rules.append(rule("c", enabled=False))
    report = replay([env(1)], rules)
    reasons = {s.rule_id: (s.reason, s.message) for s in report.skipped}
    assert reasons["b"][0] == GROUP_LOST and "a" in reasons["b"][1]
    assert reasons["c"][0] == DISABLED
    assert [w.rule_id for w in report.would_fire] == ["a"]


def test_rule_filter_keeps_other_rules_in_the_snapshot():
    rules = [rule("a", supersedes=("b",)), rule("b")]
    report = replay([env(1)], rules, rule_id="b")
    assert not report.would_fire
    assert [(s.rule_id, s.reason) for s in report.skipped] == [("b", "superseded_by")]
    with pytest.raises(ReplayError):
        replay([env(1)], rules, rule_id="nope")


def test_reads_events_collection_in_order_and_limit():
    store = MemoryStore()
    for i in (3, 1, 2):
        store.insert(EVENTS_COLLECTION, event_document(env(i), host="h"))
    report = replay(store, [rule("a")])
    assert report.events == 3
    assert report.to_dict()["events"] == 3
    assert len(replay(store, [rule("a")], limit=2).would_fire) == 2


def test_replay_executes_zero_actions_and_writes_nothing():
    # replay takes no actor ports at all, so "zero actions" is proven on the store: no run,
    # claim, audit entry, fire marker or cursor is written, and the events are untouched
    store = MemoryStore()
    for i in range(3):
        store.insert(EVENTS_COLLECTION, event_document(env(i), host="h"))
    watched = (EVENTS_COLLECTION, *RUN_COLLECTIONS, FIRES_COLLECTION, CURSOR_COLLECTION)
    head = {c: store.head(c) for c in watched}
    before = {c: copy.deepcopy(store.find(c, {}, limit=1000)) for c in watched}
    report = replay(store, [rule("a")])
    assert len(report.would_fire) == 3
    assert report.actions_executed == 0
    assert {c: store.find(c, {}, limit=1000) for c in watched} == before
    assert all(list(store.changes(c, head[c])) == [] for c in watched)
    assert store.find("runs", {}, limit=10) == []


def test_replay_does_not_mutate_inputs():
    envs = [env(1)]
    snap = copy.deepcopy(envs)
    replay(envs, [rule("a")])
    assert envs == snap


def test_bad_inputs_raise():
    with pytest.raises(ReplayError):
        replay([{"kind": "event"}], [rule("a")])
    with pytest.raises(ReplayError):
        replay([env(1)], [rule("a")], limit=0)
