"""c22 / h19: ``record_decision`` writes with a single insert, reading only on a duplicate."""

from __future__ import annotations

from culture_rules.engine.decisions import RULE_DECISIONS, decision_key, record_decision
from culture_rules.engine.matching import CONDITION_FALSE, FIRE, SUPERSEDED_BY, Decision
from culture_rules.store.memory import MemoryStore

AT = "2026-10-04T00:00:00.000000+00:00"


class Spy:
    """Wraps a store and records every operation name called on it."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        target = getattr(self.store, name)

        def call(*args, **kw):
            self.calls.append(name)
            return target(*args, **kw)

        return call


def skip(rule_id: str = "b") -> Decision:
    return Decision(rule_id=rule_id, fire=False, reason=SUPERSEDED_BY, by=("a",))


def test_a_new_record_is_one_insert_without_a_read():
    spy = Spy(MemoryStore())
    doc = record_decision(spy, skip(), event_id="evt_1", host="spark", at=AT)
    assert spy.calls == ["insert"]
    assert doc["id"] == decision_key("b", "evt_1")
    assert doc["reason"] == SUPERSEDED_BY
    assert spy.store.get(RULE_DECISIONS, doc["id"])["by"] == ["a"]


def test_a_duplicate_reads_and_answers_the_existing_record_unchanged():
    store = MemoryStore()
    first = record_decision(store, skip(), event_id="evt_1", host="spark", at=AT)
    spy = Spy(store)
    again = record_decision(spy, skip(), event_id="evt_1", host="thor", at="later")
    assert spy.calls == ["insert", "get"]
    assert again["host"] == "spark"
    assert again["at"] == first["at"]
    assert len(store.find(RULE_DECISIONS)) == 1


def test_fires_and_unrecorded_skips_touch_nothing():
    spy = Spy(MemoryStore())
    fire = Decision(rule_id="b", fire=True, reason=FIRE)
    quiet = Decision(rule_id="b", fire=False, reason=CONDITION_FALSE)
    assert record_decision(spy, fire, event_id="evt_1", host="spark", at=AT) is None
    assert record_decision(spy, quiet, event_id="evt_1", host="spark", at=AT) is None
    assert spy.calls == []
