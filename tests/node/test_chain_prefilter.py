"""#7: the chain feed opens no transaction for a finished run nothing depends on."""

from __future__ import annotations

from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.model.action import Action
from culture_rules.model.rule import Rule, Trigger
from culture_rules.node.chain import FeedConsumer, Source
from culture_rules.store.memory import MemoryStore


class SpyStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.transactions = 0

    def transaction(self):
        self.transactions += 1
        return super().transaction()


def rule(rid: str, **kw) -> Rule:
    return Rule(
        id=rid,
        name=rid,
        trigger=Trigger(kind="event", params={"type": "t"}),
        action=Action(kind="noop", params={}),
        **kw,
    )


def run_doc(rule_id: str, n: int) -> dict:
    return {"id": f"run-{rule_id}-{n}", "rule": {"id": rule_id}, "status": "succeeded"}


def consumer(store, handled: list) -> FeedConsumer:
    source = Source(
        RUNS_COLLECTION,
        lambda doc: doc["id"],
        lambda tx, doc, marker: handled.append(doc["id"]),
    )
    return FeedConsumer(store, (source,), host="h", consumer="chain-test")


def test_a_run_with_no_dependants_opens_no_transaction_and_the_cursor_advances():
    store = SpyStore()
    store.put("rules", rule("a").to_dict())
    store.put("rules", rule("b", must_after=("a",)).to_dict())
    handled: list[str] = []
    c = consumer(store, handled)
    assert c.poll() == []  # pins the head
    store.put(RUNS_COLLECTION, run_doc("b", 1))  # nothing depends on b
    before = store.transactions
    assert c.poll() == []
    assert store.transactions == before
    assert handled == []
    token = store.load_cursor("chain-test", RUNS_COLLECTION)
    assert list(store.changes(RUNS_COLLECTION, token)) == []  # the cursor moved past it
    assert c.poll() == []  # not redelivered


def test_a_run_with_a_dependant_still_fires_and_a_new_dependant_is_seen_next_poll():
    store = SpyStore()
    store.put("rules", rule("a").to_dict())
    handled: list[str] = []
    c = consumer(store, handled)
    c.poll()
    store.put(RUNS_COLLECTION, run_doc("a", 1))
    c.poll()
    assert handled == []  # no dependant yet
    store.put("rules", rule("b", may_after=("a",)).to_dict())
    store.put(RUNS_COLLECTION, run_doc("a", 2))
    assert len(c.poll()) == 1  # snapshot refreshed on the next poll
    assert handled == ["run-a-2"]
