"""Characterization tests (Sonar S3776 split of d21 code): pin ``chain_hold.decline``'s
early returns, partial and full release, and contention exactly as they behave, so
splitting it into helpers is provably behaviour-preserving."""

from __future__ import annotations

import pytest

from culture_rules.engine.chain_hold import decline
from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS, budget_id
from culture_rules.store.memory import MemoryStore
from culture_rules.store.port import TransientStoreError

KEY = "pr:o/r#1"
DOC = budget_id(KEY)


class _Lost:
    won = False


def _event(**data):
    return {"id": "e1", "data": {"concurrency_key": KEY, "run_id": "run-1", **data}}


def _store(hold=None, run_id="run-1", revision=3):
    store = MemoryStore()
    doc = {"id": DOC, "key": KEY, "run_id": run_id, "revision": revision}
    if hold is not None:
        doc["hold"] = hold
    store.insert(RULE_ATTEMPT_BUDGETS, doc)
    return store


HOLD = {"run_id": "run-1", "rules": ["a", "b"], "until": "x"}


@pytest.mark.parametrize(
    "envelope",
    [
        {},
        {"data": "x"},
        {"data": {"concurrency_key": KEY}},
        {"data": {"run_id": "run-1"}},
        {"data": {"concurrency_key": 1, "run_id": "run-1"}},
    ],
)
def test_an_event_without_a_key_and_run_is_left(envelope):
    store = _store(HOLD)
    assert decline(store, envelope, ["a", "b"], ["a", "b"]) is None
    assert store.get(RULE_ATTEMPT_BUDGETS, DOC)["hold"] == HOLD


@pytest.mark.parametrize(
    "store",
    [
        MemoryStore(),
        _store(None),
        _store("x"),
        _store({**HOLD, "run_id": "run-0"}),
        _store(HOLD, run_id="run-2"),  # a continuation took the key
    ],
)
def test_no_hold_of_this_run_is_left(store):
    before = store.get(RULE_ATTEMPT_BUDGETS, DOC)
    assert decline(store, _event(), ["a", "b"], ["a", "b"]) is None
    assert store.get(RULE_ATTEMPT_BUDGETS, DOC) == before


def test_nothing_to_remove_writes_nothing():
    store = _store(HOLD)
    before = store.get(RULE_ATTEMPT_BUDGETS, DOC)
    assert decline(store, _event(), ["c"], ["a", "b", "c"]) is None
    assert store.get(RULE_ATTEMPT_BUDGETS, DOC) == before


def test_a_decided_or_dead_rule_leaves_the_hold():
    store = _store(HOLD)
    assert decline(store, _event(), iter(["a"]), iter(["a", "b"])) is None
    doc = store.get(RULE_ATTEMPT_BUDGETS, DOC)
    assert (doc["hold"], doc["revision"]) == ({**HOLD, "rules": ["b"]}, 4)
    assert "hold_released" not in doc
    assert decline(store, _event(), [], ["a"]) == DOC  # b is no longer live
    doc = store.get(RULE_ATTEMPT_BUDGETS, DOC)
    assert (doc["hold"], doc["hold_released"], doc["revision"]) == (None, "run-1", 5)


def test_a_hold_without_rules_list_reads_as_none():
    store = _store({"run_id": "run-1", "rules": None})
    assert decline(store, _event(), ["a"], ["a"]) is None
    assert store.get(RULE_ATTEMPT_BUDGETS, DOC)["hold"] == {"run_id": "run-1", "rules": None}


def test_a_lost_write_rereads_then_releases():
    store = _store(HOLD, revision=None)
    real = store.update_if
    seen = []

    def lose_once(collection, id, expected, changes, **kw):
        seen.append((dict(expected), dict(changes)))
        if len(seen) == 1:
            return _Lost()
        return real(collection, id, expected, changes, **kw)

    store.update_if = lose_once
    assert decline(store, _event(), ["a", "b"], ["a", "b"]) == DOC
    assert (
        seen
        == [
            ({"revision": None}, {"revision": 1, "hold": None, "hold_released": "run-1"}),
        ]
        * 2
    )


def test_sustained_contention_raises():
    store = _store(HOLD)
    calls = []

    def always_lose(collection, id, expected, changes, **kw):
        calls.append(id)
        return _Lost()

    store.update_if = always_lose
    event = _event()
    with pytest.raises(TransientStoreError, match="chain hold release contention"):
        decline(store, event, ["a"], ["a", "b"])
    assert len(calls) > 1
    assert set(calls) == {DOC}
