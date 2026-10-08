"""Characterization tests (Sonar S3776 split of d21 code): pin ``firing._recover_trigger``'s
breadth-first walk - its records, order, visited set, cycles and depth bound - exactly as it
behaves, so splitting it into helpers is provably behaviour-preserving."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from culture_rules.engine.claims import firing_key
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.node import firing
from culture_rules.node.firing import RULE_FIRES, _recover_trigger, run_id_for
from culture_rules.store.memory import MemoryStore

EV = "e1"
ENV = {"id": EV, "type": "t"}


def _rule(id, must=(), may=()):
    return SimpleNamespace(id=id, must_after=tuple(must), may_after=tuple(may))


def _run(store, rule_id, trigger=ENV):
    store.insert(RUNS_COLLECTION, {"id": run_id_for(rule_id, EV), "trigger": trigger})


def _intent(store, rule_id, trigger=ENV):
    store.insert(RULE_FIRES, {"id": firing_key(rule_id, EV), "trigger": trigger})


class _Counting(MemoryStore):
    def __init__(self):
        super().__init__()
        self.reads = []

    def get(self, collection, id):
        self.reads.append((collection, id))
        return super().get(collection, id)


@pytest.mark.parametrize("event_id", [None, "", 5])
def test_no_event_id_recovers_nothing(event_id):
    store = _Counting()
    assert _recover_trigger(store, [_rule("r")], "r", event_id) == {}
    assert store.reads == []


def test_the_rules_own_run_comes_before_its_intent():
    store = MemoryStore()
    _run(store, "r", {**ENV, "from": "run"})
    _intent(store, "r", {**ENV, "from": "intent"})
    assert _recover_trigger(store, [_rule("r")], "r", EV)["from"] == "run"


def test_an_intent_is_read_when_the_run_has_none_and_a_wrong_id_is_skipped():
    store = MemoryStore()
    _run(store, "r", {"id": "other"})
    _intent(store, "r")
    out = _recover_trigger(store, [_rule("r")], "r", EV)
    assert out == ENV
    out["x"] = 1  # a copy
    assert store.get(RULE_FIRES, firing_key("r", EV))["trigger"] == ENV


def test_predecessors_are_walked_breadth_first_each_once_in_id_order():
    store = _Counting()
    rules = [_rule("r", must=["q", "p"]), _rule("p", must=["q"]), _rule("q", may=["s"])]
    _intent(store, "s")
    assert _recover_trigger(store, rules, "r", EV) == ENV
    order = [i for c, i in store.reads if c == RUNS_COLLECTION]
    assert order == [run_id_for(x, EV) for x in ("r", "p", "q", "s")]


def test_a_cycle_or_an_unknown_rule_ends_the_walk():
    store = _Counting()
    rules = [_rule("r", must=["p"]), _rule("p", may=["r", "gone"])]
    assert _recover_trigger(store, rules, "r", EV) == {}
    assert len(store.reads) == 6  # r, p, gone: run and intent each


def test_the_walk_is_bounded_by_the_recovery_depth(monkeypatch):
    monkeypatch.setattr(firing, "RECOVERY_DEPTH", 2)
    rules = [_rule("r0", must=["r1"]), _rule("r1", must=["r2"]), _rule("r2", must=["r3"])]
    store = MemoryStore()
    _intent(store, "r2")
    assert _recover_trigger(store, rules, "r0", EV) == ENV
    deeper = MemoryStore()
    _intent(deeper, "r3")
    assert _recover_trigger(deeper, rules, "r0", EV) == {}
