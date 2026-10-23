"""#7: the chain feed opens no transaction for a finished run nothing depends on."""

from __future__ import annotations

from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.model.action import Action
from culture_rules.model.rule import Rule, Trigger
from culture_rules.node.chain import FeedConsumer, Source
from culture_rules.store.memory import MemoryStore
from culture_rules.store.port import CURSOR_COLLECTION, QUIET_CURSOR_EVERY_S


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


# ---- a quiet source: the cursor follows the feed's end, at most every QUIET_CURSOR_EVERY_S --


class CursorSpyStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.saved: list[str] = []

    def save_cursor(self, consumer, collection, token):
        self.saved.append(token)
        super().save_cursor(consumer, collection, token)

    def update_if(self, collection, id, expected, changes, **kw):
        result = super().update_if(collection, id, expected, changes, **kw)
        if collection == CURSOR_COLLECTION and result.won:
            self.saved.append(changes["token"])
        return result


class Ticks:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _quiet_consumer(store, handled, ticks, name="chain-test") -> FeedConsumer:
    source = Source(
        RUNS_COLLECTION, lambda doc: doc["id"], lambda tx, d, m: handled.append(d["id"])
    )
    return FeedConsumer(store, (source,), host="h", consumer=name, quiet_ticks=ticks)


def test_a_quiet_source_cursor_moves_past_other_collections_writes():
    """#7 follow-up: a feed nothing lands on must not be re-read from an old point on every
    poll (on MongoDB that rescanned days of oplog and pinned a core per node)."""
    store = CursorSpyStore()
    store.put("rules", rule("a").to_dict())
    store.put("rules", rule("b", must_after=("a",)).to_dict())
    handled: list[str] = []
    ticks = Ticks()
    c = _quiet_consumer(store, handled, ticks)
    c.poll()  # pins the head
    pinned = store.load_cursor("chain-test", RUNS_COLLECTION)
    for i in range(3):
        store.put("variables_other", {"id": f"v{i}"})
    c.poll()  # within five minutes of start: the head stays pinned
    assert store.load_cursor("chain-test", RUNS_COLLECTION) == pinned
    assert store.saved == []

    ticks.now += QUIET_CURSOR_EVERY_S
    c.poll()
    moved = store.load_cursor("chain-test", RUNS_COLLECTION)
    assert moved != pinned
    assert store.saved == [moved]

    for i in range(3):  # more quiet writes within five minutes: no new cursor write
        store.put("variables_other", {"id": f"w{i}"})
        ticks.now += 60
        c.poll()
    assert len(store.saved) == 1

    ticks.now += QUIET_CURSOR_EVERY_S
    c.poll()
    assert len(store.saved) == 2
    assert store.load_cursor("chain-test", RUNS_COLLECTION) != moved

    store.put(RUNS_COLLECTION, run_doc("a", 1))  # b must run after a: delivered as before
    c.poll()
    assert handled == ["run-a-1"]


def test_a_quiet_move_never_overwrites_a_cursor_another_node_moved_meanwhile():
    """A shared cursor: node A read an empty feed, node B then committed a later cursor;
    A's quiet save must not move it back (it is conditional on the cursor A started from)."""
    store = MemoryStore()
    ticks = Ticks()
    a = _quiet_consumer(store, [], ticks, name="shared")
    a.poll()  # pins the head
    store.put("variables_other", {"id": "v1"})
    ticks.now += QUIET_CURSOR_EVERY_S

    real_changes = store.changes
    committed: list[str] = []

    def b_commits_meanwhile(collection, after, **kw):
        feed = real_changes(collection, after, **kw)
        store.put("variables_other", {"id": "v2"})  # the feed moves on past A's read
        committed.append(store.head(RUNS_COLLECTION))
        store.save_cursor("shared", RUNS_COLLECTION, committed[0])  # node B commits it
        return feed

    store.changes = b_commits_meanwhile
    a.poll()
    assert committed
    assert store.load_cursor("shared", RUNS_COLLECTION) == committed[0]
