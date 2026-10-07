"""A holding run's completion racing a trigger transaction that deduplicates behind it.

Risks r19 and r18 of the pr-fixer plan: on MongoDB (snapshot isolation) a trigger
transaction can read the key's holding run as active, and a chain consumer can handle that
run's completion before the trigger transaction commits. Unless the completion handler
writes the budget document, nothing conflicts, the trigger commits a pending deduplicated
event *after* the completion was handled, and - when the consumer that owns the pending
rule is the one that already handled it (r19), or no consumer owned the holder at all
because its rule was deleted (r18) - no consumer ever fires it.

The interleaving is driven deterministically: :class:`Racing` wraps the store one trigger
consumer opens its transactions on, and once - just before that transaction's first
budget write, after it read the holder as active - ends the holding run and lets the other
consumer handle the completion. On MongoDB the transactions are real (two clients); on
MemoryStore, whose transactions are serialised, :class:`_SnapshotTx` emulates snapshot
isolation (reads at the snapshot taken when the transaction opens, first committer wins
on every written document).
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS
from culture_rules.engine.decisions import RULE_DECISIONS, decision_key
from culture_rules.engine.runs import ACTION_STEP, RUNS_COLLECTION
from culture_rules.model.placement import Placement
from culture_rules.model.rule import Rule
from culture_rules.node.daemon import Node
from culture_rules.node.firing import RULE_FIRES
from culture_rules.store.memory import MemoryStore
from culture_rules.store.port import TransientStoreError
from tests.engine.run_helpers import Clock, FakeActor, enrol_online, machine
from tests.events.fakes import FakeEventSource
from tests.node.test_concurrency_budget import COMMENT, SETTLED, event, keyed
from tests.node.test_node import BEATS, Cluster

HOST = "spark"


class _Pausing:
    """A transaction handle that runs ``racing.hook`` once, before its first budget write."""

    def __init__(self, tx: Any, racing: Racing) -> None:
        self._tx = tx
        self._racing = racing

    def update_if(self, collection: str, *args: Any, **kw: Any) -> Any:
        if collection == RULE_ATTEMPT_BUDGETS:
            self._racing.fire_hook()
        return self._tx.update_if(collection, *args, **kw)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tx, name)


class _SnapshotTx:
    """Snapshot isolation over a MemoryStore: the body runs on a private copy of the data
    taken when the transaction opens; at commit, a document the body wrote that another
    transaction changed meanwhile aborts it (first committer wins), as on MongoDB."""

    def __init__(self, base: MemoryStore) -> None:
        self.base = base

    @contextmanager
    def open(self, racing: Racing) -> Iterator[Any]:
        backend = self.base._backend  # noqa: SLF001 - a test harness over the memory store
        with backend.lock:
            snapshot = copy.deepcopy(backend.data)
        fork = MemoryStore()
        fork._backend.data = copy.deepcopy(snapshot)  # noqa: SLF001
        with fork.transaction() as tx:
            yield _Pausing(tx, racing)
        written = {
            (collection, doc_id): doc
            for collection, docs in fork._backend.data.items()  # noqa: SLF001
            for doc_id, doc in docs.items()
            if snapshot.get(collection, {}).get(doc_id) != doc
        }
        with backend.lock:
            for (collection, doc_id), _doc in written.items():
                if backend.data.get(collection, {}).get(doc_id) != snapshot.get(collection, {}).get(
                    doc_id
                ):
                    raise TransientStoreError(f"write conflict on {collection}/{doc_id}")
            with self.base.transaction() as btx:
                for (collection, _doc_id), doc in written.items():
                    btx.put(collection, doc)


class Racing:
    """The store a trigger consumer is given: everything goes to ``store`` except
    transactions, which open on ``begin`` and run ``hook`` once (see :class:`_Pausing`)."""

    def __init__(self, store: Any, begin: Callable[[Racing], Any]) -> None:
        self._store = store
        self._begin = begin
        self.hook: Callable[[], None] | None = None

    def fire_hook(self) -> None:
        hook, self.hook = self.hook, None
        if hook is not None:
            hook()

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self._begin(self) as tx:
            yield tx

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


class MongoCluster(Cluster):
    """:class:`~tests.node.test_node.Cluster` on the disposable MongoDB replica set."""

    def __init__(self, binding: Any) -> None:
        self.binding = binding
        self.clock = Clock()
        self.base = binding.make_store()
        enrol_online(self.base, self.clock, machine(HOST))
        self.actor = FakeActor(default=lambda inp, ctx: {})
        self.sources = {HOST: FakeEventSource(name=f"sub@{HOST}")}
        self.evaluations = []
        self.nodes = {HOST: self.node(HOST)}

    def peer(self) -> Any:
        return self.binding.open_peer(self.base, "1.0")

    def node(self, host: str, **kw: Any) -> Node:
        return Node(
            self.peer(),
            host,
            actors={"*": self.actor},
            event_source=self.sources[host],
            clock=self.clock,
            heartbeat_options=BEATS,
            **kw,
        )


@pytest.fixture(params=["memory", pytest.param("mongo", marks=pytest.mark.mongo)])
def backend(request):
    if request.param == "memory":
        c = Cluster(HOST)

        def begin(racing: Racing) -> Any:
            return _SnapshotTx(c.base).open(racing)

        yield c, begin
        return
    pytest.importorskip("pymongo", reason="pymongo (culture-rules[store]) is not installed")
    mongo_tests = pytest.importorskip("tests.store.test_mongo", reason="Mongo binding missing")
    mongo_tests.get_rig()  # skips when docker / the image is unavailable
    c = MongoCluster(mongo_tests.TestMongoStore())
    peer = c.peer()

    @contextmanager
    def begin(racing: Racing) -> Iterator[Any]:
        with peer.transaction() as tx:
            yield _Pausing(tx, racing)

    try:
        yield c, begin
    finally:
        mongo_tests._release_stores()


def _rule(rid: str, typ: str, *, placed: bool) -> dict:
    doc = keyed(rid, typ)
    if placed:
        doc = {**doc, "placement": Placement(machine=HOST).to_dict()}
    return Rule.from_dict(doc).to_dict()


def _race(c: Cluster, begin: Callable, *, recorder_placed: bool, delete_holder: bool) -> dict:
    """Holder rule H (checks settled) holds the key with an active run; recorder rule R
    (a comment) is deduplicated behind it by a trigger transaction that read the run as
    active - and meanwhile the run fails and the *other* consumer kind handles its end."""
    c.base.put("rules", _rule("H", SETTLED, placed=not recorder_placed))
    c.base.put("rules", _rule("R", COMMENT, placed=recorder_placed))
    c.start()
    c.actor.on(ACTION_STEP, ("accept",))
    c.cycle()  # pin every consumer's feed position before anything happens
    c.publish({**event(1, conclusion="failure"), "type": SETTLED})
    c.cycle()
    run = c.run("H", "evt_1")
    assert run is not None and run["status"] not in ("succeeded", "failed")
    if delete_holder:
        c.base.put("rules", {**c.base.get("rules", "H"), "deleted_at": "2026-10-07T00:00:00Z"})
    firing = c.nodes[HOST].firing
    trigger = firing.placed if recorder_placed else firing.shared
    # The completion is handled meanwhile by the recorder's own chain consumer: it does not
    # own the holder (r19: the holder is the other kind; r18: its rule is gone).
    early = firing.chain_placed if recorder_placed else firing.chain_shared
    racing = Racing(trigger.store, begin)

    def complete_meanwhile() -> None:
        c.base.update_if(RUNS_COLLECTION, run["id"], {}, {"status": "failed"})
        early.poll()

    racing.hook = complete_meanwhile
    trigger.store = racing
    c.publish({**event(2), "type": COMMENT})
    first = c.cycle()
    assert racing.hook is None, "the race did not happen"
    for _ in range(3):
        c.cycle()
    return {"first": first, "run": run}


CASES = [
    pytest.param(False, False, id="r19-shared-recorder-placed-holder"),
    pytest.param(True, False, id="r19-placed-recorder-shared-holder"),
    pytest.param(False, True, id="r18-deleted-holder"),
]


@pytest.mark.parametrize("recorder_placed,delete_holder", CASES)
def test_deduplicated_event_racing_the_holders_completion_is_never_stranded(
    backend, recorder_placed, delete_holder
):
    c, begin = backend
    raced = _race(c, begin, recorder_placed=recorder_placed, delete_holder=delete_holder)
    errors = [e for report in raced["first"].values() for e in report.errors]
    assert any(e.startswith("TransientStoreError") for e in errors), errors
    (doc,) = c.base.find(RULE_ATTEMPT_BUDGETS)
    budget = {k: doc.get(k) for k in ("rule_id", "run_id", "count", "pending_event_id")}
    budget["pending_rule_id"] = doc.get("pending_rule_id")
    # The trigger transaction conflicted with the completion's budget write and re-ran: it
    # saw the run ended and admitted the event (never a pending event nobody fires).
    assert c.run("R", "evt_2") is not None, budget
    assert budget["pending_event_id"] is None, budget
    assert budget["count"] == 2
    record = c.base.get(RULE_DECISIONS, decision_key("R", "evt_2"))
    assert record is None or record.get("reason") != "deduplicated", record
    assert len(c.base.find(RULE_FIRES, {"rule_id": "R"})) == 1


def test_non_owner_guard_keeps_the_pending_event_for_its_owner():
    """A consumer that is not the pending rule's owner writes the guard but leaves the
    pending event recorded: the owner's consumer fires it, exactly once."""
    c = Cluster(HOST)
    c.base.put("rules", _rule("H", SETTLED, placed=True))
    c.base.put("rules", _rule("R", COMMENT, placed=False))
    c.start()
    c.actor.on(ACTION_STEP, ("accept",))
    c.publish({**event(1, conclusion="failure"), "type": SETTLED})
    c.cycle()
    run = c.run("H", "evt_1")
    c.publish({**event(2), "type": COMMENT})
    c.cycle()
    (before,) = c.base.find(RULE_ATTEMPT_BUDGETS)
    assert before["pending_event_id"] == "evt_2" and before["pending_rule_id"] == "R"
    c.clock.advance(1)
    c.base.update_if(RUNS_COLLECTION, run["id"], {}, {"status": "failed"})
    firing = c.nodes[HOST].firing
    firing.chain_placed.poll()  # not R's consumer: the guard only
    (guarded,) = c.base.find(RULE_ATTEMPT_BUDGETS)
    assert guarded["revision"] == before["revision"] + 1
    assert guarded["pending_event_id"] == "evt_2" and guarded["run_id"] == run["id"]
    firing.chain_shared.poll()  # R's consumer releases and fires it
    for _ in range(3):
        c.cycle()
    assert c.run("R", "evt_2") is not None
    assert len(c.base.find(RULE_FIRES, {"rule_id": "R"})) == 1
    (after,) = c.base.find(RULE_ATTEMPT_BUDGETS)
    assert after["count"] == 2 and after["pending_event_id"] is None


def test_guard_writes_only_while_the_run_holds_the_key():
    from culture_rules.engine.claims import guard_concurrency, reserve_concurrency

    store = MemoryStore()
    assert reserve_concurrency(store, "a", "pr", "run1", "intent1", None) is None
    (budget,) = store.find(RULE_ATTEMPT_BUDGETS)
    guard_concurrency(store, budget["id"], "run1")
    (guarded,) = store.find(RULE_ATTEMPT_BUDGETS)
    stamp = "updated_at"  # the store's own write metadata
    assert {**guarded, stamp: None} == {**budget, "revision": budget["revision"] + 1, stamp: None}
    guard_concurrency(store, budget["id"], "other")  # no longer the holder: no write
    assert store.find(RULE_ATTEMPT_BUDGETS) == [guarded]
